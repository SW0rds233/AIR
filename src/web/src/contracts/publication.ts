/**
 * 交付物契约: 交付包清单、产物行与稿件追溯。
 *
 * `DeliveryManifest` 对应交付包里的 `manifest.json` (由 `research/package.py` 写出),
 * 是论文/追溯视图的**权威**契约; 视图层允许在它之上放宽 (见 `views/paper.ts`
 * 的 `DeliveryManifestView`), 但字段含义只在这里定义一次。
 */

/** 交付清单 (manifest.json)。 */
export interface DeliveryManifest {
  project_id: string;
  problem_id: string;
  run_id: string;
  branch_id?: string;
  snapshot_id: string;
  delivery_level: string;
  delivery_gate_passed?: boolean | null;
  writing_map: Record<string, string>;
  source_set: {
    source_set_id: string;
    source_policy: string;
    queries: string[];
    uncovered: string[];
    documents: Array<{ source_id: string; title: string; file_hash: string; locator: string }>;
    note: string;
  };
  model_config: Record<string, string>;
  budget_limits: Record<string, number>;
  usage: Record<string, unknown>;
  manuscript_traceability: {
    ok: boolean;
    mapped: Array<{ claim_id: string; anchor: string }>;
    unmapped_claims: string[];
    missing_anchors: string[];
    note: string;
  };
}

export interface ArtifactEntry {
  name: string;
  size?: number;
  problem_id?: string;
  run_id?: string;
  delivery_level?: string;
}

/** 交付物清单响应 (`GET /api/artifacts`)。 */
export interface ArtifactListBody {
  files?: ArtifactEntry[];
  roots?: string[];
  [key: string]: unknown;
}

/** 清单读取结果: 要么拿到清单, 要么拿到一句**可显示的原因**。 */
export interface ManifestReadResult {
  manifest: DeliveryManifest | null;
  reason: string;
}

/** 从产物清单里挑出交付包 manifest (按 basename 精确匹配, 不靠下标)。 */
export function pickManifestEntry(
  files: ArtifactEntry[] | null | undefined,
): ArtifactEntry | null {
  const list = Array.isArray(files) ? files : [];
  return list.find(
    (entry) => String(entry?.name || '').split('/').pop() === 'manifest.json',
  ) ?? null;
}

/**
 * 判定"读到的 manifest 响应"是否可用。
 *
 * 后端 `GET /api/artifacts/{name}` 有三种可能: 文本内容 (`content` 是字符串)、
 * 二进制 (`binary` 为真, 不该当 JSON 解析)、或错误 (`error`)。这里把三种都翻译成
 * "要么拿到清单, 要么拿到一句能显示的原因" —— 面板据此**如实说明**, 而不是显示空数据。
 */
export function readManifestBody(body: unknown): ManifestReadResult {
  if (!body || typeof body !== 'object') {
    return { manifest: null, reason: 'manifest 响应不是对象' };
  }
  const payload = body as Record<string, unknown>;
  if (payload.error) {
    return { manifest: null, reason: String(payload.error) };
  }
  if (typeof payload.content === 'string') {
    try {
      return { manifest: JSON.parse(payload.content) as DeliveryManifest, reason: '' };
    } catch (error) {
      return { manifest: null, reason: 'manifest 不是合法 JSON: ' + String(error) };
    }
  }
  if (payload.binary) {
    return { manifest: null, reason: 'manifest 不是可读文本 (返回了二进制)' };
  }
  // 有些路径直接返回解析好的对象 (后端两种返回形态都出现过)
  return { manifest: payload as unknown as DeliveryManifest, reason: '' };
}
