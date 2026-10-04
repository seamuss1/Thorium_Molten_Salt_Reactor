"""Shared transient boundaries; events are right-continuous controls."""

import math
from collections.abc import Iterator, Mapping, Sequence


def transient_intervals(
    duration: float, step: float, events: Sequence[Mapping]
) -> Iterator[tuple[float, float, float]]:
    """Yield (start, end, dt), including an initial zero-length observation."""
    if not math.isfinite(duration) or duration < 0 or not math.isfinite(step) or step <= 0:
        raise ValueError("Transient duration must be finite/non-negative and step finite/positive.")
    boundaries = {0.0, duration}
    boundaries.update(i * step for i in range(1, math.ceil(duration / step)) if i * step < duration)
    for event in events:
        time = float(event["time_s"])
        if not math.isfinite(time):
            raise ValueError("Event times must be finite.")
        if 0 <= time <= duration:
            boundaries.add(time)
    previous = 0.0
    yield 0.0, 0.0, 0.0
    for end in sorted(boundaries - {0.0}):
        yield previous, end, end - previous
        previous = end


def apply_events(controls: dict, events: Sequence[Mapping], time: float) -> None:
    aliases = {"reactivity_step_pcm": "reactivity_pcm", "secondary_sink_temp_offset_c": "sink_temp_offset_c"}
    for event in events:
        if float(event["time_s"]) <= time:
            for key, value in event.items():
                target = aliases.get(key, key)
                if target in controls:
                    controls[target] = float(value)
