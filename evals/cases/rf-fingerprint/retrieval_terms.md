# 检索领域词表（数据文件，代码不再内置任何领域知识）

> 用途：相关性过滤的**领域知识全部来自本文件**，代码只保留与领域无关的
> `mentions(terms, text)` 与"同词异域"判定。新增领域 = 新增一份本文件，
> **不需要改任何代码**。
>
> 每个字段用 `;` 或 `,` 分隔、可写中英变体；英文按词边界匹配（`rf` 不会命中
> `performance` 里的 `rf`），中文按子串匹配。
>
> `domain` 只用于把**主题文本**匹配到本文件（slug 或别名命中即采用）。

domain: rf-fingerprint
aliases: 射频指纹, 射频指纹识别, rf fingerprint, rf fingerprinting, radio frequency fingerprint

## 1. 领域信号词（in_domain）

出现即视为"该领域内的文本"：

in_domain: rf; radio; wireless; emitter; fingerprint; spectrum; transmitter; receiver; physical layer; modulation; drone; uav; iot; bluetooth; wifi; wi-fi; lte; zigbee; device identification; cognitive radio; radar; specific emitter; signal; communication; 射频; 指纹; 辐射源; 发射机; 无线; 通信; 信号; 频谱; 电台; 电子对抗

## 2. 同词异域术语（off_domain_confusables）

**同一个词在另一领域有完全不同的含义**时，标题命中右侧术语即可疑；只有
`strong_in_domain` 词**就近出现**（同一标题内、间隔不超过 40 字）才判为该领域内的边缘工作。
宁可放过，不可误删。

`rf fingerprint` / `射频指纹` 的经典混淆：`website fingerprinting`（Tor 流量分析）
与射频指纹（物理层）共用 "fingerprint" 一词。

strong_in_domain: rf; radio; wireless; emitter; 射频; 无线; 辐射源; 电台
off_domain_confusables: audio; acoustic; speech; music; multimedia; video; image; face; website; malware; recording device; 网站指纹; 浏览器指纹; tor traffic

## 3. 非技术性来源

venue 命中即剔除（教学/人文类来源混入技术综述）：

non_technical_venues: 教学; 教育; 人文; 社会研究; 课程; 教改; 课堂
