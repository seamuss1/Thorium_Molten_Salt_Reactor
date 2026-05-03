import { FormEvent, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, PlaySquare, ShieldCheck, SlidersHorizontal } from "lucide-react";
import { api } from "../api";
import { ExpandableText } from "../components/ExpandableText";
import type { EditableParameter, SimulationDraft } from "../types";

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
  const location = useLocation();
  const queryClient = useQueryClient();
  const session = useQuery({ queryKey: ["me"], queryFn: api.me, retry: false });
  const cases = useQuery({ queryKey: ["cases"], queryFn: api.cases });
  const initialCase = new URLSearchParams(location.search).get("case");
  const [caseName, setCaseName] = useState(initialCase ?? "");
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [phases, setPhases] = useState(["run", "validate", "report"]);
  const [scenario, setScenario] = useState("");
  const [sweepSamples, setSweepSamples] = useState(65536);
  const [sweepSeed, setSweepSeed] = useState(42);
  const [preferGpu, setPreferGpu] = useState(true);

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
      scenario: scenario || null,
      sweep_samples: sweepSamples,
      sweep_seed: sweepSeed,
      prefer_gpu: preferGpu
    };
    createRun.mutate(draft);
  }

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <p className="eyebrow">Draft-per-run inputs</p>
          <h1>Simulation builder</h1>
        </div>
      </header>
      <form className="builder-layout" onSubmit={submit}>
        <section className="builder-controls">
          <label className="field">
            <span>Case</span>
            <select value={caseName} onChange={(event) => setCaseName(event.target.value)}>
              {cases.data?.map((item) => (
                <option key={item.name} value={item.name}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
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
          <div className="builder-options">
            <label className="field">
              <span>Scenario</span>
              <select value={scenario} onChange={(event) => setScenario(event.target.value)}>
                <option value="">Default</option>
                {scenarios?.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Sweep samples</span>
              <input type="number" min={1} max={65536} value={sweepSamples} onChange={(event) => setSweepSamples(Number(event.target.value))} />
            </label>
            <label className="field">
              <span>Sweep seed</span>
              <input type="number" min={0} value={sweepSeed} onChange={(event) => setSweepSeed(Number(event.target.value))} />
            </label>
            <label className="check-line">
              <input type="checkbox" checked={preferGpu} onChange={(event) => setPreferGpu(event.target.checked)} />
              <span>Use GPU for sweep</span>
            </label>
          </div>
          <button className="primary-action wide" type="submit" disabled={startDisabled}>
            <PlaySquare aria-hidden="true" />
            <span>{createRun.isPending ? "Starting..." : "Start run"}</span>
          </button>
          <div className={session.data?.can_start_run === false ? "error-box" : "draft-note"}>
            <ShieldCheck aria-hidden="true" />
            <span>{quotaLabel}</span>
          </div>
          {createRun.error && <div className="error-box">{createRun.error.message}</div>}
        </section>
        <section className="builder-parameters">
          <div className="section-title">
            <SlidersHorizontal aria-hidden="true" />
            <h2>Input parameters</h2>
          </div>
          {[...groupedParameters.entries()].map(([group, parameters]) => (
            <div key={group} className="parameter-band">
              <h3>{group}</h3>
              <div className="input-grid">
                {parameters.map((parameter) => (
                  <label key={parameter.path} className="field">
                    <ExpandableText className="field-title" insideInteractive lines={2}>
                      {parameter.label}
                    </ExpandableText>
                    <input
                      type="number"
                      min={parameter.minimum ?? undefined}
                      max={parameter.maximum ?? undefined}
                      step={inputStepForParameter(parameter)}
                      value={String(values[parameter.path] ?? parameter.value ?? "")}
                      onChange={(event) => setValues((current) => ({ ...current, [parameter.path]: Number(event.target.value) }))}
                    />
                    <ExpandableText className="field-hint" insideInteractive lines={1}>
                      {parameter.unit ?? parameter.path}
                    </ExpandableText>
                  </label>
                ))}
              </div>
            </div>
          ))}
          <div className="draft-note">
            <CheckCircle2 aria-hidden="true" />
            <span>Submitted values are written to the new bundle snapshot only.</span>
          </div>
        </section>
      </form>
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
  return parameter.kind === "integer" ? 1 : "any";
}
