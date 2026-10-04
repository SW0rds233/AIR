/**
 * 论文视图的**公共类型与转义**: 三个子视图共用的那一点东西。
 *
 * 为什么单独一个文件: `traceability` / `figure-gallery` / `numbering` 都要用
 * `DeliveryManifestView` 与 `esc`。放在任一子视图里都会让另外两个反向依赖它
 * (图表面板为了拿一个 `esc` 去 import 追溯表 —— 那是错的依赖方向)。
 *
 * 传输契约的唯一来源仍是 `contracts/publication.ts`; 这里只声明**页面真正读取但
 * 契约尚未收**的字段, 不另立平行契约。
 */

import type { DeliveryManifest, ArtifactEntry } from '../../contracts/publication';

/** 最小转义: 视图只往 DOM 里塞文本, 不解析 HTML。 */
export function esc(text: unknown): string {
  return String(text ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/** 交付包里可反查性检查的结果 (`manifest.manuscript_traceability`)。 */
export interface ManuscriptTraceability {
  ok?: boolean;
  mapped?: Array<{ claim_id?: string; anchor?: string }>;
  unmapped_claims?: string[];
  missing_anchors?: string[];
  unlabeled_blocks?: string[];
  core_claims?: number;
  note?: string;
  error?: string;
}

/**
 * 页面读取的 manifest 视图。
 *
 * `DeliveryManifest` 是**权威**契约; 这个视图在其之上放宽两处:
 * - 页面可能读到契约尚未收的字段 (`gate_passed` / `theory_gate_passed` /
 *   `claims` / `evidence` / `created_at`), 均为可选;
 * - `manuscript_traceability` 收窄成结构化类型 (契约里只声明了它的一部分)。
 *
 * 由契约继承来的必填字段在这里全部可选 —— 页面拿到的是**磁盘上的 JSON**,
 * 缺字段必须显示"未知", 不能靠类型断言假装它一定存在。
 */
export type DeliveryManifestView = Partial<Omit<DeliveryManifest, 'manuscript_traceability'>> & {
  created_at?: string;
  claims?: number;
  evidence?: number;
  gate_passed?: boolean | null;
  theory_gate_passed?: boolean | null;
  manuscript_traceability?: ManuscriptTraceability;
};

/** 交付物清单条目 (页面只读取这些字段)。 */
export type ArtifactEntryView = ArtifactEntry & {
  mtime?: number;
  package_dir?: string;
  unattributed?: boolean;
  /** 归属: 交付物清单按 package 根系到具体项目/问题/运行 (server._artifact_owner)。 */
  project_id?: string;
  problem_id?: string;
  run_id?: string;
};
