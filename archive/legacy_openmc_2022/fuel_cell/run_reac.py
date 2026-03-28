import matplotlib
import numpy as np
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

graphite = openmc.Material(2,'graphite')
graphite.add_element("C",1.0)

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
core = openmc.ZCylinder(r=50.8)

R = [0+f*9.03 for f in range(6)]
N = [1,6,12,18,24,30]
fuel_dic = dict()
pipe_inner_dic = dict()
pipe_outer_dic = dict()
gap_outer_dic = dict()
bodies = []
for n,r in zip(R,N):
	t = np.linspace(0,2*np.pi,int(n), endpoint=False)
	X = r*np.cos(t)
	Y=r*np.sin(t)
	if X.size==0:
		(X,Y) = ([0], [0])
	for x,y in zip(X,Y):
		key = str(x)+'_'+str(y)
		fs_outer_radius = openmc.ZCylinder(x0=x, y0=y, r=7.6703, boundary_type='transmission')
		pipe_inner_radius = openmc.ZCylinder(x0=x, y0=y, r=3.67665)
		pipe_outer_radius = openmc.ZCylinder(x0=x, y0=y, r=380365)
#		gap_outer_radius = openmc.ZCylinder(x0=x, y0=y, r=3.805)
#		bodies.append(fs_outer_radius)
#		bodies.append(pipe_inner_radius)
#		bodies.append(pipe_outer_radius)
		fuel_dic[key] = fs_outer_radius
		pipe_inner_dic[key] = pipe_inner_radius
		pipe_outer_dic[key] = pipe_outer_radius
#		gap_outer_dic[key] = gap_outer_radius

regions = []
for key,value in fuel_dic.items():
#	gap_region = +fuel_dic[key] & -pipe_inner_dic[key]
#	gap = openmc.Cell(name='air gap')
#	gap.region = gap_region

#	gap_region2 = -gap_outer_dic[key] & +pipe_inner_dic[key]
#	gap2 = openmc.Cell(name='air gap')
#	gap.region = gap_region

	fuel_region = -fuel_dic[key]
	pipe_region = +pipe_inner_dic[key] & -pipe_outer_dic[key]

	fuel = openmc.Cell(name='fuel')
	fuel.fill = fuel_salt
	fuel.region = fuel_region

	piping = openmc.Cell(name='piping')
	piping.fill = pipe
	piping.region = pipe_region

#	regions.append(gap_region)
	regions.append(fuel_region)
	regions.append(pipe_region)

#	bodies.append(gap)
	bodies.append(fuel)
	bodies.append(piping)

pitch = 203.2
left = openmc.XPlane(x0=-pitch/2, boundary_type='reflective')
right = openmc.XPlane(x0=pitch/2, boundary_type='reflective')
bottom = openmc.YPlane(y0=-pitch/2, boundary_type='reflective')
top = openmc.YPlane(y0=pitch/2, boundary_type='reflective')

s='-core & '
#for key,value in gap_outer_dic.items():
#	s+=f'+gap_outer_dic[\'{key}\'] & '
#print(s)
#core_region = [-core & +pipe_outer_dic[f] for f in pipe_outer_dic]
#core_region=-core & +gap_outer_dic['6.0_0.0'] & +gap_outer_dic['4.596266658713868_3.8567256581192355'] & +gap_outer_dic['1.0418890660015825_5.908846518073248'] & +gap_outer_dic['-2.9999999999999987_5.196152422706632'] & +gap_outer_dic['-5.63815572471545_2.0521208599540133'] & +gap_outer_dic['-5.638155724715451_-2.052120859954012'] & +gap_outer_dic['-3.0000000000000027_-5.19615242270663'] & +gap_outer_dic['1.0418890660015798_-5.908846518073249'] & +gap_outer_dic['4.5962666587138665_-3.8567256581192373'] & +gap_outer_dic['12.0_0.0'] & +gap_outer_dic['11.276311449430901_4.104241719908025'] & +gap_outer_dic['9.192533317427737_7.713451316238471'] & +gap_outer_dic['6.000000000000002_10.392304845413264'] & +gap_outer_dic['2.083778132003165_11.817693036146496'] & +gap_outer_dic['-2.0837781320031636_11.817693036146496'] & +gap_outer_dic['-5.999999999999997_10.392304845413264'] & +gap_outer_dic['-9.192533317427735_7.713451316238474'] & +gap_outer_dic['-11.2763114494309_4.1042417199080266'] & +gap_outer_dic['-12.0_1.4695761589768238e-15'] & +gap_outer_dic['-11.276311449430901_-4.104241719908024'] & +gap_outer_dic['-9.192533317427737_-7.713451316238471'] & +gap_outer_dic['-6.000000000000005_-10.39230484541326'] & +gap_outer_dic['-2.083778132003164_-11.817693036146496'] & +gap_outer_dic['2.0837781320031596_-11.817693036146498'] & +gap_outer_dic['6.000000000000002_-10.392304845413264'] & +gap_outer_dic['9.192533317427733_-7.713451316238475'] & +gap_outer_dic['11.276311449430898_-4.104241719908034'] & +gap_outer_dic['18.0_0.0'] & +gap_outer_dic['17.514807670436827_4.151085673363923'] & +gap_outer_dic['16.08538752582142_8.078385243608318'] & +gap_outer_dic['13.788799976141604_11.570176974357707'] & +gap_outer_dic['10.748854650650152_14.438217469590787'] & +gap_outer_dic['7.129435788704824_16.52788992384493'] & +gap_outer_dic['3.1256671980047477_17.726539554219745'] & +gap_outer_dic['-1.046606920388564_17.96954684888283'] & +gap_outer_dic['-5.162458188799624_17.2438112216788'] & +gap_outer_dic['-8.999999999999996_15.588457268119896'] & +gap_outer_dic['-12.352349481637203_13.09272554831488'] & +gap_outer_dic['-15.038780605432853_9.891161605274512'] & +gap_outer_dic['-16.91446717414635_6.15636257986204'] & +gap_outer_dic['-17.878290439354974_2.0896724542541465'] & +gap_outer_dic['-17.878290439354974_-2.089672454254142'] & +gap_outer_dic['-16.914467174146353_-6.156362579862035'] & +gap_outer_dic['-15.038780605432857_-9.891161605274508'] & +gap_outer_dic['-12.352349481637205_-13.092725548314876'] & +gap_outer_dic['-9.000000000000007_-15.58845726811989'] & +gap_outer_dic['-5.162458188799632_-17.243811221678797'] & +gap_outer_dic['-1.0466069203885724_-17.96954684888283'] & +gap_outer_dic['3.1256671980047397_-17.726539554219748'] & +gap_outer_dic['7.129435788704817_-16.527889923844935'] & +gap_outer_dic['10.748854650650145_-14.438217469590791'] & +gap_outer_dic['13.7887999761416_-11.570176974357713'] & +gap_outer_dic['16.085387525821417_-8.078385243608324'] & +gap_outer_dic['17.514807670436827_-4.151085673363928'] & +gap_outer_dic['24.0_0.0'] & +gap_outer_dic['23.63538607229299_4.167556264006328'] & +gap_outer_dic['22.552622898861802_8.20848343981605'] & +gap_outer_dic['20.784609690826528_11.999999999999998'] & +gap_outer_dic['18.385066634855473_15.426902632476942'] & +gap_outer_dic['15.426902632476946_18.385066634855473'] & +gap_outer_dic['12.000000000000004_20.784609690826528'] & +gap_outer_dic['8.208483439816051_22.5526228988618'] & +gap_outer_dic['4.16755626400633_23.63538607229299'] & +gap_outer_dic['1.4695761589768238e-15_24.0'] & +gap_outer_dic['-4.167556264006327_23.63538607229299'] & +gap_outer_dic['-8.20848343981605_22.552622898861802'] & +gap_outer_dic['-11.999999999999995_20.784609690826528'] & +gap_outer_dic['-15.426902632476946_18.385066634855473'] & +gap_outer_dic['-18.38506663485547_15.426902632476947'] & +gap_outer_dic['-20.784609690826528_11.999999999999998'] & +gap_outer_dic['-22.5526228988618_8.208483439816053'] & +gap_outer_dic['-23.63538607229299_4.167556264006336'] & +gap_outer_dic['-24.0_2.9391523179536475e-15'] & +gap_outer_dic['-23.635386072292995_-4.16755626400632'] & +gap_outer_dic['-22.552622898861802_-8.208483439816048'] & +gap_outer_dic['-20.78460969082653_-11.999999999999993'] & +gap_outer_dic['-18.385066634855473_-15.426902632476942'] & +gap_outer_dic['-15.426902632476947_-18.38506663485547'] & +gap_outer_dic['-12.00000000000001_-20.78460969082652'] & +gap_outer_dic['-8.208483439816066_-22.5526228988618'] & +gap_outer_dic['-4.167556264006328_-23.63538607229299'] & +gap_outer_dic['-4.408728476930472e-15_-24.0'] & +gap_outer_dic['4.167556264006319_-23.635386072292995'] & +gap_outer_dic['8.208483439816035_-22.552622898861806'] & +gap_outer_dic['12.000000000000004_-20.784609690826528'] & +gap_outer_dic['15.426902632476942_-18.385066634855477'] & +gap_outer_dic['18.385066634855466_-15.42690263247695'] & +gap_outer_dic['20.78460969082652_-12.00000000000001'] & +gap_outer_dic['22.552622898861795_-8.208483439816067'] & +gap_outer_dic['23.63538607229299_-4.167556264006329'] & +gap_outer_dic['30.0_0.0'] & +gap_outer_dic['29.70804206224711_4.175193028801963'] & +gap_outer_dic['28.837850878149567_8.269120674509974'] & +gap_outer_dic['27.406363729278027_12.202099292274006'] & +gap_outer_dic['25.44144288469278_15.897577926996147'] & +gap_outer_dic['22.98133329356934_19.283628290596177'] & +gap_outer_dic['20.073918190765745_22.294344764321828'] & +gap_outer_dic['16.775787104122404_24.871127176651253'] & +gap_outer_dic['13.151134403672323_26.96382138897501'] & +gap_outer_dic['9.270509831248424_28.531695488854606'] & +gap_outer_dic['5.209445330007912_29.544232590366242'] & +gap_outer_dic['1.0469849010750325_29.981724810572874'] & +gap_outer_dic['-3.1358538980296067_29.8356568610482'] & +gap_outer_dic['-7.2576568679900335_29.108871788279895'] & +gap_outer_dic['-11.238197802477362_27.81551563700362'] & +gap_outer_dic['-14.999999999999993_25.98076211353316'] & +gap_outer_dic['-18.469844259769747_23.64032260820166'] & +gap_outer_dic['-21.580194010159534_20.839751113769914'] & +gap_outer_dic['-24.27050983124842_17.633557568774197'] & +gap_outer_dic['-26.48842778576781_14.084146883576722'] & +gap_outer_dic['-28.19077862357725_10.260604299770065'] & +gap_outer_dic['-29.34442802201417_6.23735072453278'] & +gap_outer_dic['-29.926921507794727_2.0926942123237655'] & +gap_outer_dic['-29.926921507794727_-2.0926942123237584'] & +gap_outer_dic['-29.344428022014167_-6.237350724532785'] & +gap_outer_dic['-28.190778623577252_-10.26060429977006'] & +gap_outer_dic['-26.488427785767808_-14.084146883576725'] & +gap_outer_dic['-24.270509831248425_-17.63355756877419'] & +gap_outer_dic['-21.58019401015953_-20.83975111376992'] & +gap_outer_dic['-18.469844259769744_-23.640322608201664'] & +gap_outer_dic['-15.000000000000014_-25.980762113533153'] & +gap_outer_dic['-11.23819780247737_-27.81551563700362'] & +gap_outer_dic['-7.2576568679900335_-29.108871788279895'] & +gap_outer_dic['-3.135853898029601_-29.835656861048204'] & +gap_outer_dic['1.0469849010750385_-29.981724810572874'] & +gap_outer_dic['5.209445330007899_-29.544232590366242'] & +gap_outer_dic['9.270509831248416_-28.53169548885461'] & +gap_outer_dic['13.151134403672323_-26.96382138897501'] & +gap_outer_dic['16.77578710412241_-24.87112717665125'] & +gap_outer_dic['20.073918190765735_-22.29434476432184'] & +gap_outer_dic['22.981333293569335_-19.283628290596187'] & +gap_outer_dic['25.44144288469278_-15.897577926996151'] & +gap_outer_dic['27.40636372927803_-12.202099292274005'] & +gap_outer_dic['28.837850878149567_-8.269120674509969'] & +gap_outer_dic['29.708042062247106_-4.175193028801976'] 
#core_region = -core & +pipe_outer_dic['6.0_0.0'] & +pipe_outer_dic['4.596266658713868_3.8567256581192355'] & +pipe_outer_dic['1.0418890660015825_5.908846518073248'] & +pipe_outer_dic['-2.9999999999999987_5.196152422706632'] & +pipe_outer_dic['-5.63815572471545_2.0521208599540133'] & +pipe_outer_dic['-5.638155724715451_-2.052120859954012'] & +pipe_outer_dic['-3.0000000000000027_-5.19615242270663'] & +pipe_outer_dic['1.0418890660015798_-5.908846518073249'] & +pipe_outer_dic['4.5962666587138665_-3.8567256581192373'] & +pipe_outer_dic['12.0_0.0'] & +pipe_outer_dic['11.276311449430901_4.104241719908025'] & +pipe_outer_dic['9.192533317427737_7.713451316238471'] & +pipe_outer_dic['6.000000000000002_10.392304845413264'] & +pipe_outer_dic['2.083778132003165_11.817693036146496'] & +pipe_outer_dic['-2.0837781320031636_11.817693036146496'] & +pipe_outer_dic['-5.999999999999997_10.392304845413264'] & +pipe_outer_dic['-9.192533317427735_7.713451316238474'] & +pipe_outer_dic['-11.2763114494309_4.1042417199080266'] & +pipe_outer_dic['-12.0_1.4695761589768238e-15'] & +pipe_outer_dic['-11.276311449430901_-4.104241719908024'] & +pipe_outer_dic['-9.192533317427737_-7.713451316238471'] & +pipe_outer_dic['-6.000000000000005_-10.39230484541326'] & +pipe_outer_dic['-2.083778132003164_-11.817693036146496'] & +pipe_outer_dic['2.0837781320031596_-11.817693036146498'] & +pipe_outer_dic['6.000000000000002_-10.392304845413264'] & +pipe_outer_dic['9.192533317427733_-7.713451316238475'] & +pipe_outer_dic['11.276311449430898_-4.104241719908034'] & +pipe_outer_dic['18.0_0.0'] & +pipe_outer_dic['17.514807670436827_4.151085673363923'] & +pipe_outer_dic['16.08538752582142_8.078385243608318'] & +pipe_outer_dic['13.788799976141604_11.570176974357707'] & +pipe_outer_dic['10.748854650650152_14.438217469590787'] & +pipe_outer_dic['7.129435788704824_16.52788992384493'] & +pipe_outer_dic['3.1256671980047477_17.726539554219745'] & +pipe_outer_dic['-1.046606920388564_17.96954684888283'] & +pipe_outer_dic['-5.162458188799624_17.2438112216788'] & +pipe_outer_dic['-8.999999999999996_15.588457268119896'] & +pipe_outer_dic['-12.352349481637203_13.09272554831488'] & +pipe_outer_dic['-15.038780605432853_9.891161605274512'] & +pipe_outer_dic['-16.91446717414635_6.15636257986204'] & +pipe_outer_dic['-17.878290439354974_2.0896724542541465'] & +pipe_outer_dic['-17.878290439354974_-2.089672454254142'] & +pipe_outer_dic['-16.914467174146353_-6.156362579862035'] & +pipe_outer_dic['-15.038780605432857_-9.891161605274508'] & +pipe_outer_dic['-12.352349481637205_-13.092725548314876'] & +pipe_outer_dic['-9.000000000000007_-15.58845726811989'] & +pipe_outer_dic['-5.162458188799632_-17.243811221678797'] & +pipe_outer_dic['-1.0466069203885724_-17.96954684888283'] & +pipe_outer_dic['3.1256671980047397_-17.726539554219748'] & +pipe_outer_dic['7.129435788704817_-16.527889923844935'] & +pipe_outer_dic['10.748854650650145_-14.438217469590791'] & +pipe_outer_dic['13.7887999761416_-11.570176974357713'] & +pipe_outer_dic['16.085387525821417_-8.078385243608324'] & +pipe_outer_dic['17.514807670436827_-4.151085673363928'] & +pipe_outer_dic['24.0_0.0'] & +pipe_outer_dic['23.63538607229299_4.167556264006328'] & +pipe_outer_dic['22.552622898861802_8.20848343981605'] & +pipe_outer_dic['20.784609690826528_11.999999999999998'] & +pipe_outer_dic['18.385066634855473_15.426902632476942'] & +pipe_outer_dic['15.426902632476946_18.385066634855473'] & +pipe_outer_dic['12.000000000000004_20.784609690826528'] & +pipe_outer_dic['8.208483439816051_22.5526228988618'] & +pipe_outer_dic['4.16755626400633_23.63538607229299'] & +pipe_outer_dic['1.4695761589768238e-15_24.0'] & +pipe_outer_dic['-4.167556264006327_23.63538607229299'] & +pipe_outer_dic['-8.20848343981605_22.552622898861802'] & +pipe_outer_dic['-11.999999999999995_20.784609690826528'] & +pipe_outer_dic['-15.426902632476946_18.385066634855473'] & +pipe_outer_dic['-18.38506663485547_15.426902632476947'] & +pipe_outer_dic['-20.784609690826528_11.999999999999998'] & +pipe_outer_dic['-22.5526228988618_8.208483439816053'] & +pipe_outer_dic['-23.63538607229299_4.167556264006336'] & +pipe_outer_dic['-24.0_2.9391523179536475e-15'] & +pipe_outer_dic['-23.635386072292995_-4.16755626400632'] & +pipe_outer_dic['-22.552622898861802_-8.208483439816048'] & +pipe_outer_dic['-20.78460969082653_-11.999999999999993'] & +pipe_outer_dic['-18.385066634855473_-15.426902632476942'] & +pipe_outer_dic['-15.426902632476947_-18.38506663485547'] & +pipe_outer_dic['-12.00000000000001_-20.78460969082652'] & +pipe_outer_dic['-8.208483439816066_-22.5526228988618'] & +pipe_outer_dic['-4.167556264006328_-23.63538607229299'] & +pipe_outer_dic['-4.408728476930472e-15_-24.0'] & +pipe_outer_dic['4.167556264006319_-23.635386072292995'] & +pipe_outer_dic['8.208483439816035_-22.552622898861806'] & +pipe_outer_dic['12.000000000000004_-20.784609690826528'] & +pipe_outer_dic['15.426902632476942_-18.385066634855477'] & +pipe_outer_dic['18.385066634855466_-15.42690263247695'] & +pipe_outer_dic['20.78460969082652_-12.00000000000001'] & +pipe_outer_dic['22.552622898861795_-8.208483439816067'] & +pipe_outer_dic['23.63538607229299_-4.167556264006329'] & +pipe_outer_dic['30.0_0.0'] & +pipe_outer_dic['29.70804206224711_4.175193028801963'] & +pipe_outer_dic['28.837850878149567_8.269120674509974'] & +pipe_outer_dic['27.406363729278027_12.202099292274006'] & +pipe_outer_dic['25.44144288469278_15.897577926996147'] & +pipe_outer_dic['22.98133329356934_19.283628290596177'] & +pipe_outer_dic['20.073918190765745_22.294344764321828'] & +pipe_outer_dic['16.775787104122404_24.871127176651253'] & +pipe_outer_dic['13.151134403672323_26.96382138897501'] & +pipe_outer_dic['9.270509831248424_28.531695488854606'] & +pipe_outer_dic['5.209445330007912_29.544232590366242'] & +pipe_outer_dic['1.0469849010750325_29.981724810572874'] & +pipe_outer_dic['-3.1358538980296067_29.8356568610482'] & +pipe_outer_dic['-7.2576568679900335_29.108871788279895'] & +pipe_outer_dic['-11.238197802477362_27.81551563700362'] & +pipe_outer_dic['-14.999999999999993_25.98076211353316'] & +pipe_outer_dic['-18.469844259769747_23.64032260820166'] & +pipe_outer_dic['-21.580194010159534_20.839751113769914'] & +pipe_outer_dic['-24.27050983124842_17.633557568774197'] & +pipe_outer_dic['-26.48842778576781_14.084146883576722'] & +pipe_outer_dic['-28.19077862357725_10.260604299770065'] & +pipe_outer_dic['-29.34442802201417_6.23735072453278'] & +pipe_outer_dic['-29.926921507794727_2.0926942123237655'] & +pipe_outer_dic['-29.926921507794727_-2.0926942123237584'] & +pipe_outer_dic['-29.344428022014167_-6.237350724532785'] & +pipe_outer_dic['-28.190778623577252_-10.26060429977006'] & +pipe_outer_dic['-26.488427785767808_-14.084146883576725'] & +pipe_outer_dic['-24.270509831248425_-17.63355756877419'] & +pipe_outer_dic['-21.58019401015953_-20.83975111376992'] & +pipe_outer_dic['-18.469844259769744_-23.640322608201664'] & +pipe_outer_dic['-15.000000000000014_-25.980762113533153'] & +pipe_outer_dic['-11.23819780247737_-27.81551563700362'] & +pipe_outer_dic['-7.2576568679900335_-29.108871788279895'] & +pipe_outer_dic['-3.135853898029601_-29.835656861048204'] & +pipe_outer_dic['1.0469849010750385_-29.981724810572874'] & +pipe_outer_dic['5.209445330007899_-29.544232590366242'] & +pipe_outer_dic['9.270509831248416_-28.53169548885461'] & +pipe_outer_dic['13.151134403672323_-26.96382138897501'] & +pipe_outer_dic['16.77578710412241_-24.87112717665125'] & +pipe_outer_dic['20.073918190765735_-22.29434476432184'] & +pipe_outer_dic['22.981333293569335_-19.283628290596187'] & +pipe_outer_dic['25.44144288469278_-15.897577926996151'] & +pipe_outer_dic['27.40636372927803_-12.202099292274005'] & +pipe_outer_dic['28.837850878149567_-8.269120674509969'] & +pipe_outer_dic['29.708042062247106_-4.175193028801976']
core_region = -core
print('Initial',core_region)
for name in fuel_dic:
	core_region = core_region & +pipe_outer_dic[name] & +pipe_inner_dic[name] & +fuel_dic[name]
void_region = +left & -right & +bottom & -top & +core
#print(core_region)
Core = openmc.Cell(name='Core')
Core.fill = graphite
Core.region = core_region
void = openmc.Cell(name='void')
void.fill = air
void.region = void_region

root_universe = openmc.Universe(cells=(Core,void))
for i in bodies:
	root_universe.add_cell(i)
print((root_universe))

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
settings.particles = 100000

settings.export_to_xml()

##Tallies
cell_filter = openmc.CellFilter(fuel)

tally = openmc.Tally(1)
tally.filters = [cell_filter]

tally.nuclides = ['U233']
tally.scores = ['total', 'fission', 'absorption', '(n,gamma)']

cell_filter2 = openmc.CellFilter(Core)
tally2 = openmc.Tally(2)
tally2.filters=[cell_filter2]
tally2.scores = ['flux']

tallies = openmc.Tallies([tally,tally2])
tallies.export_to_xml()

#cell = openmc.Cell()
#cell.region = void_region
#universe = openmc.Universe()
#universe.add_cell(cell)
#plot = universe.plot(width=(2.0, 2.0))
#plot.write_png('plot.png')

#print(dir(plot))
#print(type(universe))
#rect = [4,5,6,7]
#fig = matplotlib.figure.Figure()
#ax = matplotlib.axes.Axes(fig,rect)
#ax.add_image(plot)
#fig.savefig('plot.png')

openmc.run()

plot = openmc.Plot()
plot.filename = 'reacplot'
plot.width = (100, 100)
plot.pixels = (800, 800)
#plot.from_geometry(geometry)
plot.color_by = 'material'
plot.colors = {fuel_salt: 'yellow', graphite: 'black', pipe: 'blue',air: 'white'}
overlap_color = 'red'
plots = openmc.Plots([plot])
plots.export_to_xml()
openmc.plot_geometry()



