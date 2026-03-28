import matplotlib
import openmc

##Materials
LiF = openmc.Material(5,'LiF')
LiF.add_nuclide('Li7',1.0)
LiF.add_element('F',1.0)
BeF = openmc.Material(6,'BeF2')
BeF.add_element('Be',1.0)
BeF.add_element('F',2.0)
ZrF = openmc.Material(7,'ZrF4')
ZrF.add_element('Zr',1.0)
ZrF.add_element('F',4.0)
UF = openmc.Material(8,'UF4')
UF.add_nuclide('U233',0.05)
UF.add_nuclide('U238',0.95)
UF.add_element('F',4.0)

fuel_salt = openmc.Material.mix_materials([LiF,BeF,ZrF,UF],[0.65,0.29,0.05,0.01], 'ao')

#fuel_salt = openmc.Material(1,"fuel_salt")
#fuel_salt.add_nuclide("Li7",0.263157895)
#fuel_salt.add_nuclide("F19",0.5951417)
#fuel_salt.add_nuclide("Be9",0.117408907)
#fuel_salt.add_nuclide("Zr90",0.020242915)
#fuel_salt.add_nuclide("U233",0.004048583)
#fuel_salt.set_density('g/cm3', 10.0)

graphite = openmc.Material(2,'graphite')
graphite.add_element("C",1.0)

graphite_pipe = openmc.Material(2,'graphite')
graphite_pipe.add_element("C",1.0)

pipe = openmc.Material(3,'pipe')
pipe.add_element('Pb',1.0)
pipe.set_density('g/cm3', 17.0)

#water = openmc.Material(name="h2o")
#water.add_nuclide('H1', 2.0)
#water.add_nuclide('O16', 1.0)
#water.set_density('g/cm3', 1.0)

#water.add_s_alpha_beta('c_H_in_H2O')

air = openmc.Material(4,"air")
air.add_element('He',1.0)
#uo2.set_density('g/cm3', 0.10)
materials = openmc.Materials([fuel_salt,pipe,air,graphite])
materials.export_to_xml()

##Geometry
graphite_outer_radius = openmc.ZCylinder(r=0.25)
fs_outer_radius = openmc.ZCylinder(r=0.39)
fs_inner_radius = openmc.ZCylinder(r=0.2501)

pipe_inner_radius = openmc.ZCylinder(r=0.40)
pipe_outer_radius = openmc.ZCylinder(r=0.46)
graphite_pipe_inner_radius = openmc.ZCylinder(r=0.4601)
graphite_pipe_outer_radius = openmc.ZCylinder(r=0.60)

gap_region = +fs_outer_radius & -pipe_inner_radius
gap_region2 = +graphite_outer_radius & -fs_inner_radius
gap_region3 = +pipe_outer_radius & -graphite_pipe_inner_radius

gap = openmc.Cell(name='air gap')
gap.region = gap_region
gap2 = openmc.Cell(name='air gap')
gap2.region = gap_region2
gap3 = openmc.Cell(name='air gap')
gap3.region = gap_region3

fuel_region =  +fs_inner_radius & -fs_outer_radius
graphite_region = -graphite_outer_radius
pipe_region = +pipe_inner_radius & -pipe_outer_radius
graphite_pipe_region = +graphite_pipe_inner_radius & -graphite_pipe_outer_radius

fuel = openmc.Cell(name='fuel')
fuel.fill = fuel_salt
fuel.region = fuel_region

piping = openmc.Cell(name='piping')
piping.fill = pipe
piping.region = pipe_region


gm = openmc.Cell(name='gm')
gm.fill = graphite
gm.region = graphite_region

gp = openmc.Cell(name='gp')
gp.fill = graphite_pipe
gp.region = graphite_pipe_region

pitch = 1.26
left = openmc.XPlane(x0=-pitch/2, boundary_type='reflective')
right = openmc.XPlane(x0=pitch/2, boundary_type='reflective')
bottom = openmc.YPlane(y0=-pitch/2, boundary_type='reflective')
top = openmc.YPlane(y0=pitch/2, boundary_type='reflective')


void_region = +left & -right & +bottom & -top & +pipe_outer_radius

void = openmc.Cell(name='void')
void.fill = air
void.region = void_region

root_universe = openmc.Universe(cells=(fuel, piping, void, gap, gap2,gap3,gm,gp))

geometry = openmc.Geometry()
geometry.root_universe = root_universe

geometry.export_to_xml()

##Settings
point = openmc.stats.Point((0, 0, 0))
source = openmc.Source(space=point)
settings = openmc.Settings()
settings.source = source
settings.batches = 11
settings.inactive = 2
settings.particles = 10000

settings.export_to_xml()

##Tallies
cell_filter = openmc.CellFilter(fuel)

tally = openmc.Tally(1)
tally.filters = [cell_filter]

tally.nuclides = ['U233']
tally.scores = ['total', 'fission', 'absorption', '(n,gamma)']

cell_filter2 = openmc.CellFilter(gp)
tally2 = openmc.Tally(2)
tally2.filters=[cell_filter2]
tally2.scores = ['flux']

tallies = openmc.Tallies([tally,tally2])
tallies.export_to_xml()

cell = openmc.Cell()
cell.region = pipe_region
universe = openmc.Universe()
universe.add_cell(cell)
plot = universe.plot(width=(2.0, 2.0))
plot.write_png('plot.png')
#print(dir(plot))
#print(type(universe))
#rect = [4,5,6,7]
#fig = matplotlib.figure.Figure()
#ax = matplotlib.axes.Axes(fig,rect)
#ax.add_image(plot)
#fig.savefig('plot.png')
openmc.run()

