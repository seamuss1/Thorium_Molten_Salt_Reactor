# Reactor Taxonomy And Flagship Planning Target

This repository separates validation, research, submodel, and commercial-planning reactor cases so the output is clear about what can and cannot be treated as a real build target.

## Case Taxonomy

- `example_pin`: PWR-inspired smoke/regression pin. It verifies neutronics plumbing and is not a molten-salt reactor build target.
- `fuel_channel`: TMSR fuel-channel submodel. It is useful for local channel geometry and reaction-rate studies, not plant planning.
- `msre_*`: historic MSRE benchmark harnesses. These are validation anchors for historical molten-salt physics behavior and are not modern build candidates.
- `tmsr_lf1_core`: TMSR-LF1-inspired modern test-reactor surrogate. It bridges public operating-point evidence, OpenMC CSG geometry, and reduced-order thermal models.
- `immersed_pool_reference`: research/demonstrator primary-system reference. It carries richer loop, pool, inventory, and visualization machinery than the LF1 surrogate.
- `flagship_grid_msr`: commercial grid target. This is the first case that should be treated as the repository's finance and build-schedule subject.

## Flagship End Goal

The `flagship_grid_msr` case represents a 300 MWe net U.S. grid-connected thorium molten-salt SMR. It uses the repository's detailed molten-salt core representation as a planning bridge, then adds commercial characteristics:

- U.S. NRC Part 52 combined-license planning basis,
- one 300 MWe net module with about 792 MWth thermal power,
- fluoride thorium fuel salt with proxy U-233 breeding and cleanup assumptions,
- firm grid generation as the primary plant role,
- conservative first-of-a-kind finance and schedule assumptions.

The flagship output is a planning-grade real-world estimate. It is intentionally not a vendor quote, EPC bid, detailed licensing application, investment recommendation, or site-specific owner's estimate.

## Finance And Schedule Scope

`reactor economics flagship_grid_msr` writes:

- `finance.json` for capital, financing, O&M, fuel, annual generation, and LCOE,
- `schedule.json` for phase dates and commercial operation timing,
- `cash_flow.csv` for monthly construction spend and interest during construction,
- `cost_breakdown.csv` for capital and annual-cost rollups,
- `project_plan.json` for a concise finance/schedule summary.

Default source values are held in source-year 2022 USD. Escalation to later dollars is explicit through a configured escalation factor rather than implicit inflation.
