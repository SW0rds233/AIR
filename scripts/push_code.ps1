<#
.SYNOPSIS
    推送代码/文档提交到远端，把 evals/reports/ 下的审查报告正文永久留在本地。

.DESCRIPTION
    本仓库口径（见 evals/reports/README.md「落点与推送口径」）：

      * 审查报告写在 `outputs/research/<project_id>/` 与 `evals/reports/` 两处；
      * `evals/reports/` 那一份只提交到本地，**不推送**；
      * 代码、工具、文档照常推送。

    两者都在 `master` 上，直接 `git push` 必然把报告一起带上远端，所以推送统一走本脚本：

      1. 把 `origin/master..master` 的提交按改动分类：
         * 「报告类」= 新增/修改 `evals/reports/` 下的报告正文（`README.md` 是索引，不算正文）；
         * 「代码类」= 其余改动，含**删除**报告正文（远端树维护，例如清理历史报告）。
      2. 若有代码提交排在报告提交之上（历史交错），先在远端基点重放代码提交，
         再把报告提交重放到顶端，维持「报告永远是最上面的提交」这一不变量；
      3. 只推送代码类提交的顶端：`git push origin <tip>:refs/heads/master`；
      4. 报告提交保持本地领先，不进入远端历史。

    配合 `.githooks/pre-push`：绕过脚本直接推送、且范围里含报告提交时会被拒绝。

.PARAMETER DryRun
    只打印分组与将要推送的提交，不改写历史、不推送。

.PARAMETER Remote
    远端名，默认 origin。

.PARAMETER Branch
    分支名，默认 master。

.PARAMETER Ip
    连接 github.com 时依次尝试的 IP（443 端口常被重置，需要一个一个试）。

.EXAMPLE
    pwsh -File scripts/push_code.ps1 -DryRun
    pwsh -File scripts/push_code.ps1
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [string]$Remote = "origin",
    [string]$Branch = "master",
    [string[]]$Ip = @("20.205.243.166", "20.200.245.247", "20.27.177.113", "140.82.112.4")
)

$ErrorActionPreference = "Stop"
$ReportPrefix = "evals/reports/"
$ReportIndex = "evals/reports/README.md"
$remoteRef = "$Remote/$Branch"

function Invoke-Git {
    param([Parameter(Mandatory)][string[]]$Arg)
    $out = & git @Arg 2>&1
    return [pscustomobject]@{ Code = $LASTEXITCODE; Lines = @($out | ForEach-Object { "$_" }) }
}

function Get-Changes {
    param([string]$Sha)
    # core.quotepath=false：否则含中文的路径会被转义成 "evals/reports/\346..." 形式，分组就会判错
    $r = Invoke-Git @("-c", "core.quotepath=false", "show", "--no-renames", "--name-status", "--format=", $Sha)
    $changes = @()
    foreach ($line in $r.Lines) {
        if (-not $line -or -not $line.Trim()) { continue }
        $parts = $line -split "`t"
        if ($parts.Count -lt 2) { continue }
        $changes += [pscustomobject]@{ Status = $parts[0].Trim(); Path = $parts[1].Trim().Trim('"') }
    }
    return $changes
}

function Test-ReportCommit {
    param($Changes)
    # 报告正文的新增/修改/改名才算「报告类」；删除正文是远端树维护，跟代码一起推
    $body = @($Changes | Where-Object {
            $_.Path -like "$ReportPrefix*" -and $_.Path -ne $ReportIndex -and $_.Status -notlike "D*"
        })
    return ($body.Count -gt 0)
}

function Get-Subject {
    param([string]$Sha)
    $r = Invoke-Git @("log", "-1", "--format=%s", $Sha)
    return @($r.Lines | Where-Object { $_ })[0]
}

Push-Location (& git rev-parse --show-toplevel).Trim()
try {
    $dirty = @((Invoke-Git @("status", "--porcelain")).Lines | Where-Object { $_ -and $_ -notmatch '^\?\? ' })
    if ($dirty.Count -gt 0) {
        throw "工作区有未提交改动，先提交或 stash 再推送：`n  $($dirty -join "`n  ")"
    }

    $remoteSha = @((Invoke-Git @("rev-parse", "--verify", "--quiet", $remoteRef)).Lines | Where-Object { $_ })[0]
    if (-not $remoteSha) { throw "找不到 $remoteRef，先 git fetch $Remote $Branch" }

    $range = @((Invoke-Git @("rev-list", "--reverse", "$remoteRef..HEAD")).Lines | Where-Object { $_ })
    if ($range.Count -eq 0) {
        Write-Host "没有本地提交需要推送；本地与 $remoteRef 一致。" -ForegroundColor Green
        return
    }

    $code = @()
    $report = @()
    $deletions = @()
    $seenReport = $false
    $interleaved = $false
    foreach ($sha in $range) {
        $changes = Get-Changes -Sha $sha
        if (Test-ReportCommit -Changes $changes) {
            $seenReport = $true
            $report += $sha
        }
        else {
            if ($seenReport) { $interleaved = $true }
            $code += $sha
            $deletions += @($changes | Where-Object {
                    $_.Path -like "$ReportPrefix*" -and $_.Path -ne $ReportIndex -and $_.Status -like "D*"
                } | ForEach-Object { $_.Path })
        }
    }

    Write-Host ("本地提交：代码/文档 {0} 个，报告 {1} 个" -f $code.Count, $report.Count)
    foreach ($sha in $code) {
        Write-Host ("  [推送] {0} {1}" -f $sha.Substring(0, 7), (Get-Subject -Sha $sha))
    }
    if ($deletions.Count -gt 0) {
        Write-Host ("  [推送] 该推送会从远端树删除 {0} 份报告正文：" -f $deletions.Count) -ForegroundColor Yellow
        $deletions | ForEach-Object { Write-Host "         $_" -ForegroundColor Yellow }
    }
    foreach ($sha in $report) {
        $extra = @((Get-Changes -Sha $sha) | Where-Object { $_.Path -notlike "$ReportPrefix*" })
        if ($extra.Count -gt 0) {
            Write-Host ("  [本地] {0} {1}  ⚠ 该提交同时改了非报告路径，这些改动不会推送：{2}" -f $sha.Substring(0, 7), (Get-Subject -Sha $sha), (($extra | ForEach-Object { $_.Path }) -join ", ")) -ForegroundColor Yellow
        }
        else {
            Write-Host ("  [本地] {0} {1}" -f $sha.Substring(0, 7), (Get-Subject -Sha $sha)) -ForegroundColor DarkGray
        }
    }

    if ($code.Count -eq 0) {
        Write-Host "没有需要推送的提交；报告提交继续留在本地。" -ForegroundColor Green
        return
    }

    $originalHead = @((Invoke-Git @("rev-parse", "HEAD")).Lines)[0]
    $tip = $code[-1]

    if ($interleaved) {
        Write-Host "历史交错（代码提交在报告提交之上），先重放为「代码在下、报告在上」。" -ForegroundColor Yellow
        if ($DryRun) {
            Write-Host ("  [DryRun] 在 {0} 之上重放 {1} 个代码提交，再重放 {2} 个报告提交。" -f $remoteRef, $code.Count, $report.Count) -ForegroundColor Yellow
        }
        else {
            $r = Invoke-Git @("checkout", "--quiet", "--detach", $remoteRef)
            if ($r.Code -ne 0) { throw "checkout $remoteRef 失败：$($r.Lines -join ' ')" }
            try {
                foreach ($sha in $code) {
                    $c = Invoke-Git @("cherry-pick", $sha)
                    if ($c.Code -ne 0) { throw "重放代码提交 $sha 冲突：`n  $($c.Lines -join "`n  ")" }
                }
                $tip = @((Invoke-Git @("rev-parse", "HEAD")).Lines)[0]
                foreach ($sha in $report) {
                    $c = Invoke-Git @("cherry-pick", $sha)
                    if ($c.Code -ne 0) { throw "重放报告提交 $sha 冲突：`n  $($c.Lines -join "`n  ")" }
                }
                $b = Invoke-Git @("branch", "-f", $Branch, "HEAD")
                if ($b.Code -ne 0) { throw "更新 $Branch 失败：$($b.Lines -join ' ')" }
                $r = Invoke-Git @("checkout", "--quiet", $Branch)
                if ($r.Code -ne 0) { throw "切回 $Branch 失败：$($r.Lines -join ' ')" }
                Write-Host ("重放完成：{0} -> {1}" -f $originalHead.Substring(0, 7), $tip.Substring(0, 7))
            }
            catch {
                Invoke-Git @("cherry-pick", "--abort") | Out-Null
                Invoke-Git @("checkout", "--quiet", $Branch) | Out-Null
                throw
            }
        }
    }

    if ($DryRun) {
        Write-Host ("  [DryRun] git push {0} {1}:refs/heads/{2}" -f $Remote, $tip, $Branch) -ForegroundColor Yellow
        Write-Host "  [DryRun] 报告提交保持在本地领先。" -ForegroundColor Yellow
        return
    }

    $attempts = @(
        @{ Name = "直连"; Args = @() },
        @{ Name = "直连 + HTTP/2"; Args = @("-c", "http.version=HTTP/2") }
    )
    foreach ($address in $Ip) {
        $attempts += @{ Name = "IP $address"; Args = @("-c", "http.curloptResolve=github.com:443:$address") }
        $attempts += @{ Name = "IP $address + HTTP/2"; Args = @("-c", "http.curloptResolve=github.com:443:$address", "-c", "http.version=HTTP/2") }
    }

    $pushed = $false
    $index = 0
    foreach ($attempt in $attempts) {
        $index += 1
        Write-Host ("推送尝试 {0}/{1}：{2}" -f $index, $attempts.Count, $attempt.Name)
        $pushArgs = @($attempt.Args) + @("push", $Remote, "${tip}:refs/heads/$Branch")
        $out = & git @pushArgs 2>&1
        $pushCode = $LASTEXITCODE
        if ($pushCode -eq 0) {
            $out | ForEach-Object { Write-Host "  $_" }
            $pushed = $true
            break
        }
        $reason = @($out | Where-Object { "$_" -match 'error|fatal|rejected|failed' } | Select-Object -Last 1)[0]
        if (-not $reason) { $reason = @($out | Select-Object -Last 1)[0] }
        Write-Host ("  失败：{0}" -f $reason) -ForegroundColor DarkYellow
    }
    if (-not $pushed) { throw "推送失败：所有尝试均未成功（代码提交仍安全留在本地）。" }

    $now = @((Invoke-Git @("rev-parse", "--verify", "--quiet", $remoteRef)).Lines | Where-Object { $_ })[0]
    if ($now -ne $tip) {
        Invoke-Git @("update-ref", "refs/remotes/$Remote/$Branch", $tip) | Out-Null
    }
    $ahead = @((Invoke-Git @("rev-list", "--count", "$remoteRef..HEAD")).Lines)[0]
    $behind = @((Invoke-Git @("rev-list", "--count", "HEAD..$remoteRef")).Lines)[0]
    Write-Host ("已推送 {0}；本地领先 {1} 个提交（报告，仅本地），落后 {2} 个。" -f $tip.Substring(0, 7), $ahead, $behind) -ForegroundColor Green

    $hooks = (Invoke-Git @("config", "--local", "core.hooksPath")).Lines
    if ($hooks -notcontains ".githooks") {
        Write-Host "提示：pre-push 守卫未安装；执行 git config --local core.hooksPath .githooks 启用。" -ForegroundColor Yellow
    }
}
finally {
    Pop-Location
}
