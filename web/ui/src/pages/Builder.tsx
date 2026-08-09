import { FormEvent, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, ClipboardCheck, PlaySquare, ShieldCheck, SlidersHorizontal, XCircle } from "lucide-react";
import { api } from "../api";
import { Truncate } from "../components/Truncate";
import { PanelError, PanelLoading } from "../components/StateBlock";
import type { DraftValidationResponse, EditableParameter, SimulationDraft, SweepBackend, SweepDtype } from "../types";

const backendOptions: Array<{ value: SweepBackend; label: string; hint: string }> = [
  { value: "auto", label: "Automatic", hint: "GPU when one is available, otherwise CPU" },
  { value: "torch-xpu", label: "GPU (Intel XPU)", hint: "Fails if no XPU device is present" },
  { value: "numpy", label: "CPU (NumPy)", hint: "Always available" },
  { value: "torch-cpu", label: "CPU (PyTorch)", hint: "For comparing against the GPU path" }
];

// Ensembles below ~131,072 do not amortize the GPU's per-step overhead, so the
// form says so rather than letting people pick a size the device can't help.
const GPU_EFFICIENT_SAMPLE_FLOOR = 131072;
const MAX_SWEEP_SAMPLES = 4194304;

const phaseOptions = [
  { value: "run", label: "Dry run" },
  { value: "transient", label: "Transient" },
  { value: "transient-sweep", label: "Sweep" },
  { value: "validate", label: "Validate" },
  { value: "render", label: "Render" },
  { value: "report", label: "Report" }
];

export function Builder() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const requestedCase = searchParams.get("case");
  const queryClient = useQueryClient();
  const session = useQuery({ queryKey: ["me"], queryFn: api.me, retry: false });
  const cases = useQuery({ queryKey: ["cases"], queryFn: api.cases });
  const [caseName, setCaseName] = useState(requestedCase ?? "");
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [phases, setPhases] = useState(["run", "validate", "report"]);
  const [scenario, setScenario] = useState("");
  const [sweepSamples, setSweepSamples] = useState(65536);
  const [sweepSeed, setSweepSeed] = useState(42);
  const [sweepBackend, setSweepBackend] = useState<SweepBackend>("auto");
  const [sweepDtype, setSweepDtype] = useState<SweepDtype>("float32");

  // Honor ?case= on mount and on in-app navigation to a different case.
  useEffect(() => {
    if (requestedCase && requestedCase !== caseName) {
      setCaseName(requestedCase);
      setValues({});
    }
  }, [requestedCase]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!caseName && cases.data?.[0]) {
      setCaseName(cases.data[0].name);
    }
  }, [caseName, cases.data]);

  const detail = useQuery({
    queryKey: ["case", caseName],
    queryFn: () => api.caseDetail(caseName),
    enabled: Boolean(caseName)
  });
  const scenarios = useMemo(() => {
    const transient = detail.data?.config.transient as { scenarios?: Array<{ name?: string }> } | undefined;
    return transient?.scenarios?.map((item) => item.name).filter(Boolean) as string[] | undefined;
  }, [detail.data]);

  // Drop a scenario the newly-selected case does not define. Carrying it over
  // silently ran the case's default transient while the form still displayed
  // the old scenario name, so the run and the label disagreed.
  useEffect(() => {
    if (scenario && scenarios && !scenarios.includes(scenario)) {
      setScenario("");
    }
  }, [scenario, scenarios]);
  const groupedParameters = useMemo(() => {
    const groups = new Map<string, EditableParameter[]>();
    detail.data?.editable_parameters.forEach((parameter) => groups.set(parameter.group, [...(groups.get(parameter.group) ?? []), parameter]));
    return groups;
  }, [detail.data]);

  const createRun = useMutation({
    mutationFn: api.createRun,
    onSuccess: (run) => {
      queryClient.invalidateQueries({ queryKey: ["me"] });
      queryClient.invalidateQueries({ queryKey: ["rate-limits"] });
      navigate(`/runs/${run.case_name}/${run.run_id}`);
    }
  });
  const validation = useMutation({
    mutationFn: () => api.validateDraft(caseName, buildPatch(values))
  });

  const sweepActive = phases.includes("transient-sweep");
  const scenarioActive = phases.includes("transient") || phases.includes("transient-sweep");

  const quotaLabel = !session.data
    ? "Checking simulation access"
    : session.data.is_admin
      ? "Unlimited simulation starts"
      : `${session.data.runs_remaining_today ?? 0} of ${session.data.daily_run_limit ?? 1} starts remaining today`;
  const startDisabled = createRun.isPending || !caseName || !session.data || session.data.can_start_run === false;

  function submit(event: FormEvent) {
    event.preventDefault();
    const draft: SimulationDraft = {
      case_name: caseName,
      patch: buildPatch(values),
      phases,
      scenario: scenarioActive && scenario ? scenario : null,
      sweep_samples: sweepSamples,
      sweep_seed: sweepSeed,
      sweep_backend: sweepBackend,
      sweep_dtype: sweepDtype,
      // Kept in step with the real control so an older server still honours it.
      prefer_gpu: sweepBackend !== "numpy" && sweepBackend !== "torch-cpu"
    };
    createRun.mutate(draft);
  }

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <p className="eyebrow">Draft-per-run inputs</p>
          <h1>Builder</h1>
        </div>
      </header>
      <form className="builder-layout" onSubmit={submit}>
        <section className="builder-controls">
          <label className="field">
            <span>Case</span>
            {cases.isLoading ? (
              <PanelLoading label="Loading cases" lines={1} />
            ) : cases.isError ? (
              <PanelError error={cases.error} onRetry={() => cases.refetch()} />
            ) : (
              <select value={caseName} onChange={(event) => { setCaseName(event.target.value); setValues({}); }}>
                {cases.data?.map((item) => (
                  <option key={item.name} value={item.name}>
                    {item.name}
                  </option>
                ))}
              </select>
            )}
          </label>
          <div className="control-group">
            <div className="builder-section-label">Workflow phases</div>
            <div className="phase-grid">
              {phaseOptions.map((phase) => (
                <label key={phase.value} className="check-tile">
                  <input
                    type="checkbox"
                    checked={phases.includes(phase.value)}
                    onChange={(event) =>
                      setPhases((current) => (event.target.checked ? [...current, phase.value] : current.filter((item) => item !== phase.value)))
                    }
                  />
                  <span>{phase.label}</span>
                </label>
              ))}
            </div>
          </div>

          <div className={`control-group${scenarioActive ? "" : " is-inactive"}`}>
            <div className="builder-section-label">
              <span>Transient scenario</span>
              {!scenarioActive && <span className="inactive-hint">enable Transient phase</span>}
            </div>
            <label className="field">
              <span>Scenario</span>
              <select value={scenario} disabled={!scenarioActive} onChange={(event) => setScenario(event.target.value)}>
                <option value="">Default</option>
                {scenarios?.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <div className={`control-group${sweepActive ? "" : " is-inactive"}`}>
            <div className="builder-section-label">
              <span>Uncertainty sweep</span>
              {!sweepActive && <span className="inactive-hint">enable Sweep phase</span>}
            </div>
            <div className="builder-options">
              <label className="field">
                <span>Sweep samples</span>
                <input type="number" min={1} max={MAX_SWEEP_SAMPLES} step={1024} value={sweepSamples} disabled={!sweepActive} onChange={(event) => setSweepSamples(Number(event.target.value))} />
              </label>
              <label className="field">
                <span>Sweep seed</span>
                <input type="number" min={0} value={sweepSeed} disabled={!sweepActive} onChange={(event) => setSweepSeed(Number(event.target.value))} />
              </label>
              <label className="field">
                <span>Compute backend</span>
                <select value={sweepBackend} disabled={!sweepActive} onChange={(event) => setSweepBackend(event.target.value as SweepBackend)}>
                  {backendOptions.map((option) => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>Precision</span>
                <select value={sweepDtype} disabled={!sweepActive} onChange={(event) => setSweepDtype(event.target.value as SweepDtype)}>
                  <option value="float32">float32 (faster)</option>
                  <option value="float64">float64 (slow-timescale terms)</option>
                </select>
              </label>
              <p className="field-hint">
                {backendOptions.find((option) => option.value === sweepBackend)?.hint}
                {sweepActive && sweepBackend !== "numpy" && sweepBackend !== "torch-cpu" && sweepSamples < GPU_EFFICIENT_SAMPLE_FLOOR
                  ? ` — at ${sweepSamples.toLocaleString()} samples the GPU is barely faster than the CPU; ${GPU_EFFICIENT_SAMPLE_FLOOR.toLocaleString()} or more makes the difference count.`
                  : ""}
              </p>
            </div>
          </div>

          <div className="builder-actions">
            <button
              className="secondary-action wide"
              type="button"
              disabled={!caseName || validation.isPending}
              onClick={() => validation.mutate()}
            >
              <ClipboardCheck aria-hidden="true" />
              <span>{validation.isPending ? "Checking…" : "Check inputs"}</span>
            </button>
            <button className="primary-action wide" type="submit" disabled={startDisabled}>
              <PlaySquare aria-hidden="true" />
              <span>{createRun.isPending ? "Starting…" : "Start run"}</span>
            </button>
          </div>

          <ValidationResult data={validation.data} isError={validation.isError} error={validation.error} />

          <div className={`quota-note${session.data?.can_start_run === false ? " blocked" : ""}`}>
            <ShieldCheck aria-hidden="true" />
            <span className="quota-count">{quotaLabel}</span>
          </div>
          {createRun.error && <div className="error-box">{createRun.error.message}</div>}
        </section>

        <section className="builder-parameters">
          <div className="section-title">
            <SlidersHorizontal aria-hidden="true" />
            <h2>Input parameters</h2>
          </div>
          {detail.isLoading ? (
            <PanelLoading label="Loading parameters" lines={8} />
          ) : detail.isError ? (
            <PanelError error={detail.error} onRetry={() => detail.refetch()} />
          ) : (
            <>
              {[...groupedParameters.entries()].map(([group, parameters]) => (
                <div key={group} className="parameter-band">
                  <h3>{group}</h3>
                  <div className="input-grid">
                    {parameters.map((parameter) => (
                      <label key={parameter.path} className="field">
                        <Truncate className="field-title" lines={2}>
                          {parameter.label}
                        </Truncate>
                        {parameter.options?.length ? (
                          <select
                            value={String(values[parameter.path] ?? parameter.value ?? "")}
                            onChange={(event) => setValues((current) => ({ ...current, [parameter.path]: event.target.value }))}
                          >
                            {parameter.options.map((option) => (
                              <option key={option} value={option}>
                                {option}
                              </option>
                            ))}
                          </select>
                        ) : (
                          <input
                            type="number"
                            min={parameter.minimum ?? undefined}
                            max={parameter.maximum ?? undefined}
                            step={inputStepForParameter(parameter)}
                            value={String(values[parameter.path] ?? parameter.value ?? "")}
                            onChange={(event) => setValues((current) => ({ ...current, [parameter.path]: Number(event.target.value) }))}
                          />
                        )}
                        <Truncate className="field-hint">{parameter.unit ?? parameter.path}</Truncate>
                      </label>
                    ))}
                  </div>
                </div>
              ))}
              <div className="draft-note">
                <CheckCircle2 aria-hidden="true" />
                <span>Submitted values are written to the new bundle snapshot only — your source configs are never modified.</span>
              </div>
            </>
          )}
        </section>
      </form>
    </div>
  );
}

function ValidationResult({ data, isError, error }: { data?: DraftValidationResponse; isError: boolean; error: Error | null }) {
  if (isError) {
    return (
      <div className="validation-result invalid" role="status">
        <XCircle aria-hidden="true" />
        <div className="validation-body">
          <strong>Could not check inputs</strong>
          <span>{error?.message ?? "Validation request failed."}</span>
        </div>
      </div>
    );
  }
  if (!data) return null;
  const { valid, message, normalized_yaml } = data;
  return (
    <div className={`validation-result ${valid ? "valid" : "invalid"}`} role="status">
      {valid ? <CheckCircle2 aria-hidden="true" /> : <XCircle aria-hidden="true" />}
      <div className="validation-body">
        <strong>{valid ? "Inputs are valid" : "Inputs need attention"}</strong>
        <span>{message}</span>
        {valid && normalized_yaml && (
          <details>
            <summary>Preview normalized case</summary>
            <pre className="text-preview">{normalized_yaml}</pre>
          </details>
        )}
      </div>
    </div>
  );
}

export function buildPatch(values: Record<string, unknown>): Record<string, unknown> {
  const root: Record<string, unknown> = {};
  Object.entries(values).forEach(([path, value]) => {
    const parts = path.split(".");
    let current: Record<string, unknown> | unknown[] = root;
    parts.forEach((part, index) => {
      const isLast = index === parts.length - 1;
      const nextPart = parts[index + 1];
      const shouldBeArray = nextPart !== undefined && /^\d+$/.test(nextPart);
      if (isLast) {
        if (Array.isArray(current)) {
          current[Number(part)] = value;
        } else {
          current[part] = value;
        }
        return;
      }
      if (Array.isArray(current)) {
        const arrayIndex = Number(part);
        current[arrayIndex] = current[arrayIndex] ?? (shouldBeArray ? [] : {});
        current = current[arrayIndex] as Record<string, unknown> | unknown[];
      } else {
        current[part] = current[part] ?? (shouldBeArray ? [] : {});
        current = current[part] as Record<string, unknown> | unknown[];
      }
    });
  });
  return root;
}

export function inputStepForParameter(parameter: Pick<EditableParameter, "kind" | "step">): number | "any" {
  // Non-integer fields use step="any": the backend step (e.g. 0.01) combined
  // with the input's min forms an HTML validation grid, and shipped defaults
  // (e.g. primary_cp_kj_kgk: 4.2 against min 0.001 / step 0.01) don't lie on it,
  // so the browser would flag an untouched field as stepMismatch and block the
  // Start-run submit. Integers step by 1, which is always grid-safe.
  return parameter.kind === "integer" ? 1 : "any";
}
