export interface ArtifactRef {
  label: string;
  kind: string;
  mime_type: string;
  size: number;
  path: string;
  url: string;
}

export interface RunEvent {
  sequence: number;
  timestamp: string;
  level: string;
  phase?: string | null;
  message: string;
  progress?: number | null;
}

export interface RunRecord {
  case_name: string;
  run_id: string;
  status: string;
  phase?: string | null;
  command_plan: string[];
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  metrics: Record<string, unknown>;
  validation: Record<string, unknown>;
  provenance: Record<string, unknown>;
  reactor: Record<string, unknown>;
  capabilities: string[];
  artifacts: ArtifactRef[];
  latest_event?: RunEvent | null;
}

export interface EditableParameter {
  path: string;
  label: string;
  group: string;
  kind: string;
  value: unknown;
  unit?: string | null;
  minimum?: number | null;
  maximum?: number | null;
  step?: number | null;
  options?: string[] | null;
}

export interface CaseSummary {
  name: string;
  reactor: Record<string, unknown>;
  capabilities: string[];
  editable_parameters: EditableParameter[];
  latest_run?: RunRecord | null;
  docs: Array<{ slug: string; title: string }>;
}

export interface CaseDetail extends CaseSummary {
  config: Record<string, unknown>;
  validation_targets: Record<string, unknown>;
  benchmark_path?: string | null;
}

export interface DocSummary {
  slug: string;
  title: string;
  path: string;
  headings: string[];
}

export interface DocRecord extends DocSummary {
  content: string;
}

export interface SimulationDraft {
  case_name: string;
  run_id?: string | null;
  patch: Record<string, unknown>;
  phases: string[];
  scenario?: string | null;
  sweep_samples: number;
  sweep_seed: number;
  prefer_gpu: boolean;
}
