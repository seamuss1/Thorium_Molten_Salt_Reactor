import openmc

##Materials
fuel_salt = openmc.Material(1,"fuel_salt")
fuel_salt.add_nuclide("Li7",0.263157895)
fuel_salt.add_nuclide("F19",0.5951417)
fuel_salt.add_nuclide("Be9",0.117408907)
fuel_salt.add_nuclide("Zr90",0.020242915)
fuel_salt.add_nuclide("U233",0.004048583)

graphite = openmc.Material(2,'graphite')
graphite.add_element("C",1.0)

pipe = openmc.Material(3,'pipe')
pipe.add_element('Pb',1.0)

air = openmc.Material(4,"air")
air.add_nuclide('O16', 1.0)

materials = openmc.Materials([fuel_salt,pipe,air])
materials.export_to_xml()

##Geometry
graphite_outer_radius = openmc.ZCylinder(r=0.5)
fs_outer_radius = openmc.ZCylinder(r=0.5001)
fs_inner_radius = openmc.ZCylinder(r=1.5)

pipe_inner_radius = openmc.ZCylinder(r=1.5)
pipe_outer_radius = openmc.ZCylinder(r=5)

fuel_region = +fs_inner_radius & -fs_outer_radius
graphite_region = -graphite_outer_radius
pipe_region = +pipe_inner_radius & -pipe_outer_radius

fuel = openmc.Cell(name='fuel')
fuel.fill = fuel_salt
fuel.region = fuel_region

piping = openmc.Cell(name='piping')
piping.fill = pipe
piping.region = pipe_region

moderator = openmc.Cell(name='moderator')
moderator.fill = graphite
moderator.region = graphite_region

pitch = 126
left = openmc.XPlane(x0=-pitch/2, boundary_type='reflective')
right = openmc.XPlane(x0=pitch/2, boundary_type='reflective')
bottom = openmc.YPlane(y0=-pitch/2, boundary_type='reflective')
top = openmc.YPlane(y0=pitch/2, boundary_type='reflective')

void_region = +left & -right & +bottom & -top & +pipe_outer_radius

void = openmc.Cell(name='void')
void.fill = air
void.region = void_region

root_universe = openmc.Universe(cells=(fuel, piping, void))

geometry = openmc.Geometry()
geometry.root_universe = root_universe

geometry.export_to_xml()

##Settings
point = openmc.stats.Point((0, 0, 0))
source = openmc.Source(space=point)
settings = openmc.Settings()
settings.source = source
settings.batches = 100
settings.inactive = 10
settings.particles = 1000

settings.export_to_xml()

##Tallies
cell_filter = openmc.CellFilter(fuel)

tally = openmc.Tally(1)
tally.filters = [cell_filter]

tally.nuclides = ['U233']
tally.scores = ['total', 'fission', 'absorption', '(n,gamma)']

tallies = openmc.Tallies([tally])
tallies.export_to_xml()

openmc.run()

