import { Activity, AlertCircle, Atom, BadgeCheck, BarChart3, Box, Factory, FlaskConical, Gauge, Landmark, Waves } from "lucide-react";
import { ExpandableText } from "./ExpandableText";
import { Truncate } from "./Truncate";
import { StatusBadge } from "./StatusBadge";
import type { OutputMetric, OutputSection } from "../types";

interface RunOutputSectionsProps {
  sections: OutputSection[];
}

const sectionIcons = {
  neutronics: Atom,
  plant_balance: Factory,
  primary_flow: Waves,
  transient_response: Activity,
  transient_uncertainty: BarChart3,
  fuel_chemistry: FlaskConical,
  validation_maturity: BadgeCheck,
  commercial_planning: Landmark,
  visualization: Box
};

export function RunOutputSections({ sections }: RunOutputSectionsProps) {
  if (!sections.length) {
    return <div className="empty-panel">No detailed simulation output was found in this result bundle.</div>;
  }

  return (
    <div className="output-section-grid">
      {sections.map((section) => {
        const Icon = sectionIcons[section.id as keyof typeof sectionIcons] ?? Gauge;
        return (
          <section key={section.id} className="output-section">
            <div className="output-section-header">
              <div>
                <Icon aria-hidden="true" />
                <h2>{section.title}</h2>
              </div>
              {section.status && <StatusBadge status={section.status} dot={false} />}
            </div>

            {section.summary && (
              <ExpandableText className="output-summary" lines={2}>
                {section.summary}
              </ExpandableText>
            )}

            {!!section.metrics.length && (
              <div className="output-metric-grid">
                {section.metrics.map((metric, index) => (
                  <MetricCell key={`${metric.label}-${index}`} metric={metric} />
                ))}
              </div>
            )}

            {!!section.notes.length && (
              <ul className="output-notes">
                {section.notes.map((note) => (
                  <li key={note}>
                    <AlertCircle aria-hidden="true" />
                    <ExpandableText lines={2}>{note}</ExpandableText>
                  </li>
                ))}
              </ul>
            )}
          </section>
        );
      })}
    </div>
  );
}

function MetricCell({ metric }: { metric: OutputMetric }) {
  return (
    <div className="output-metric">
      <dt>
        <Truncate lines={2}>{metric.label}</Truncate>
      </dt>
      <dd>
        <Truncate className="output-metric-value">{formatMetricValue(metric)}</Truncate>
      </dd>
    </div>
  );
}

function formatMetricValue(metric: OutputMetric) {
  const formatted = formatValue(metric.value, metric.kind);
  return metric.unit ? `${formatted} ${metric.unit}` : formatted;
}

function formatValue(value: unknown, kind?: string | null) {
  if (value === null || value === undefined || value === "") return "n/a";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return "n/a";
    if (kind === "currency" && Math.abs(value) >= 10000) {
      return new Intl.NumberFormat(undefined, {
        currency: "USD",
        maximumFractionDigits: 1,
        notation: "compact",
        style: "currency"
      }).format(value);
    }
    if (Math.abs(value) >= 1000) {
      return new Intl.NumberFormat(undefined, { maximumSignificantDigits: 5 }).format(value);
    }
    return new Intl.NumberFormat(undefined, { maximumFractionDigits: Math.abs(value) < 10 ? 4 : 2 }).format(value);
  }
  return String(value);
}
