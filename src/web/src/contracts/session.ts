/**
 * 会话契约: 会话身份、SSE 事件与状态补偿。
 *
 * 域划分见 `src/contracts/index.ts`。这里只放"会话生命周期"相关的类型 ——
 * 它们与研究工作台 (research.ts) 和研究交付物 (publication.ts) 是三件事, 各自
 * 由不同的后端端点提供, 改动频率也不同。
 */

/** 服务端引擎标识 (仅用于显示; 前端不据此分支任何行为)。 */
export type EngineId = string;

export type RunStatus = 'idle' | 'running' | 'waiting' | 'done' | 'stopped' | 'error';

/** SSE 事件 (后端 `/api/sessions/{id}/events`)。 */
export interface SessionEvent {
  type: 'connected' | 'node' | 'log' | 'interrupt' | 'done' | 'stopped' | 'error' | string;
  text?: string;
  name?: string;
  message?: string;
  state?: Record<string, unknown>;
  payload?: Record<string, unknown>;
  _seq?: number;
}

/** 会话状态补偿接口的返回。 */
export interface SessionState {
  thread_id: string;
  session_id: string;
  status: RunStatus;
  mode: EngineId;
  run_id: string;
  project_id: string;
  problem_id: string;
  event_seq: number;
  pending_interrupt_id?: string;
  final_state?: Record<string, unknown> | null;
}

/** 启动会话的返回。 */
export interface StartSessionResponse {
  thread_id: string;
  session_id: string;
  /** 服务端引擎标识 (仅显示用)。 */
  mode: EngineId;
  run_id: string;
  project_id: string;
  problem_id: string;
  contract: import('./research').ProblemContract | null;
  spec_reused: boolean;
  resumed: boolean;
}
