"""Nimbus modular mechanical prototype R01. Millimetres.
blender -b --python design/modular-camera/build.py
Print meshes are real separate solids; coloured hardware is reference only.
"""
import bpy, bmesh, math, json, struct, zipfile, shutil
from pathlib import Path
from mathutils import Vector, Matrix

OUT=Path(__file__).resolve().parent
(OUT/'stl').mkdir(exist_ok=True)
INPUTS=OUT/'inputs'; INPUTS.mkdir(exist_ok=True)
for filename in ('01_screen_frame_192x140x4.stl','04_washer_PRINT_FOUR.stl'):
    destination=INPUTS/filename
    if not destination.exists():
        shutil.copy2(OUT.parent/'screen-holder'/'stl'/filename,destination)
SOURCE=INPUTS/'01_screen_frame_192x140x4.stl'
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
sc=bpy.context.scene
sc.unit_settings.system='METRIC'; sc.unit_settings.scale_length=.001
sc.unit_settings.length_unit='MILLIMETERS'
sc['STATUS']='R01 assembly prototype; hardware fit, power and load not yet physically tested'
sc['AXES']='X left/right, Y front(-)/screen(+), Z up; units mm'
parts=[]; instances=[]; refs=[]; hardware=[]; panel_refs=[]

def material(name,rgb):
    m=bpy.data.materials.new(name); m.diffuse_color=(*rgb,1); m.use_nodes=True
    p=m.node_tree.nodes.get('Principled BSDF'); p.inputs['Base Color'].default_value=(*rgb,1)
    p.inputs['Roughness'].default_value=.5
    return m
ivory=material('Printed ivory frame',(.72,.75,.68))
graphite=material('Printed graphite trays',(.06,.095,.10))
orange=material('Printed fastening inserts',(.95,.26,.045))
pcb=material('REFERENCE PCB / dimensions only',(.04,.27,.17))
bankmat=material('REFERENCE Anker',(.21,.27,.32))
glassmat=material('REFERENCE display',(.04,.19,.23))
black=material('REFERENCE Logitech',(.022,.027,.03))
bg=material('Studio',(.065,.09,.11))

def activate(o):
    bpy.ops.object.select_all(action='DESELECT'); o.hide_set(False); o.hide_viewport=False
    o.select_set(True); bpy.context.view_layer.objects.active=o
def box(name,loc,dims,mat=None):
    bpy.ops.mesh.primitive_cube_add(size=1,location=loc); o=bpy.context.object
    o.name=name; o.dimensions=dims
    bpy.ops.object.transform_apply(location=False,rotation=False,scale=True)
    if mat: o.data.materials.append(mat)
    return o
def cylinder(loc,r,h,axis='Z',mat=None):
    bpy.ops.mesh.primitive_cylinder_add(vertices=40,radius=r,depth=h,location=loc)
    o=bpy.context.object
    if axis=='Y': o.rotation_euler.x=math.pi/2
    if axis=='X': o.rotation_euler.y=math.pi/2
    bpy.ops.object.transform_apply(location=False,rotation=True,scale=True)
    if mat: o.data.materials.append(mat)
    return o
def boolean(o,t,op='DIFFERENCE'):
    activate(o); m=o.modifiers.new(op,'BOOLEAN'); m.operation=op; m.solver='EXACT'; m.object=t
    bpy.ops.object.modifier_apply(modifier=m.name); bpy.data.objects.remove(t,do_unlink=True)
def cut(o,loc,dims): boolean(o,box('cut',loc,dims))
def hole(o,loc,d=3.4,axis='Z',depth=30): boolean(o,cylinder(loc,d/2,depth,axis))
def slot(o,loc,length,width,axis='X',normal='Z',depth=30):
    # Rounded slot made by a box and overlapping circular ends.
    ai='XYZ'.index(axis); ni='XYZ'.index(normal); dims=[width]*3
    dims[ai]=length-width; dims[ni]=depth
    tool=box('slot',loc,dims)
    for sign in (-1,1):
        p=list(loc); p[ai]+=sign*(length-width)/2
        boolean(tool,cylinder(p,width/2,depth+2,normal),'UNION')
    boolean(o,tool)
def bake(o):
    o.data.transform(o.matrix_world); o.matrix_world=Matrix.Identity(4)
def place(o,loc=(0,0,0),rot=None):
    bake(o); o.matrix_world=Matrix.Translation(Vector(loc)) @ (rot or Matrix.Identity(4))
    return o
def register(o,name,print_rot=None,qty=1,notes=''):
    o.name=name; o['printable']=True; o['quantity']=qty
    parts.append(dict(o=o,name=name,rotation=print_rot or Matrix.Identity(4),qty=qty,notes=notes))
    instances.append(o); return o
def clone(o,name,delta=(0,0,0),matrix=None):
    c=o.copy(); c.data=o.data.copy(); sc.collection.objects.link(c); c.name=name
    if matrix is not None: c.matrix_world=matrix
    else: c.location+=Vector(delta)
    instances.append(c); return c
RX=lambda a:Matrix.Rotation(a,4,'X')
RY=lambda a:Matrix.Rotation(a,4,'Y')

# 01: reuse the tested-export screen carrier, retaining its exact mounting pattern.
bpy.ops.wm.stl_import(filepath=str(SOURCE)); screen=bpy.context.object
screen.data.materials.clear(); screen.data.materials.append(ivory)
place(screen,(0,90,100),RX(-math.pi/2))
register(screen,'01_rear_screen_frame',RX(math.pi/2),notes='Same 192x140x4 carrier as screen-holder R02. Do not reprint if already made.')

# 02: floor. 0.3 mm edge clearance avoids a forced press fit between side panels.
base=box('base',(0,0,2),(211.4,180,4),ivory)
for x in (-100,100):
    for y in (-72,0,72): hole(base,(x,y,2))
for x in (-76,76):
    for y in (-12,72): hole(base,(x,y,2))
for x in (-45,45):
    for y in (-14,74): slot(base,(x,y,2),30,4)
for x in (-75,-50,-25,0,25,50,75): slot(base,(x,-60,2),28,5,axis='Y')
register(base,'02_base')

# 03/04: open side frames, integral mounting shelves, accessible through-bolts.
sides=[]
for sign,label in [(-1,'left'),(1,'right')]:
    o=box('side',(sign*108,0,90),(4,180,180),ivory)
    # Main service windows plus low power-port opening and middle board-port opening.
    for y,d in [(-40,52),(30,36)]: cut(o,(sign*108,y,90),(30,d,120))
    cut(o,(sign*108,28,20.5),(30,100,25))
    cut(o,(sign*108,0,81),(30,164,42))
    # Rear display cable escape, no narrow connector-specific cutout.
    cut(o,(sign*108,83,101),(30,56,106))
    # Flanges under roof and above floor.
    for z in (6,174):
        for y in (-72,0,72):
            boolean(o,box('mounting lug',(sign*101,y,z),(14,16,4)),'UNION')
            hole(o,(sign*100,y,z))
    # Screen and front-panel tabs; screen fasteners never carry the chassis load.
    for y in (-88,88):
        for z in (36,164):
            boolean(o,box('panel lug',(sign*97,y,z),(22,4,16)),'UNION')
            hole(o,(sign*91,y,z),axis='Y')
    # Wide ledges for separate electronics and camera shelves.
    for z,cy,dy,ys in [(52,-20,110,(-55,15)),(109,-40,96,(-72,-20))]:
        boolean(o,box('tray ledge',(sign*101,cy,z),(14,dy,4)),'UNION')
        for y in ys: hole(o,(sign*100,y,z))
    for z in (158,166): slot(o,(sign*108,0,z),18,4,axis='Y',normal='X')
    register(o,f'{3 if sign<0 else 4:02d}_{label}_side',RY(sign*math.pi/2),notes='Print exterior flat down, lugs upwards; review small horizontal bores in slicer.')
    sides.append(o)

# 05: removable roof. Board mounts remain outside and accessible.
roof=box('roof',(0,0,178),(211.4,180,4),ivory)
for x in (-100,100):
    for y in (-72,0,72): hole(roof,(x,y,178))
roof_sites=[(-52,-45,'Buttons'),(52,-45,'Light'),(52,30,'Movement')]
for cx,cy,_ in roof_sites:
    for x in (-30,30):
        for y in (-17,17): hole(roof,(cx+x,cy+y,178))
    cut(roof,(cx,cy,178),(18,12,30))
for x in (-72,-48,-24,0): slot(roof,(x,38,178),44,5,axis='Y')
register(roof,'05_removable_roof')

# 06: easily replaced front, broad aperture exposes whole webcam face/microphone.
front=box('front',(0,-92,90),(224,4,172),ivory)
cut(front,(0,-92,138),(94,20,54))
for x in (-91,91):
    for z in (36,164): hole(front,(x,-92,z),axis='Y')
front_sites=[(-65,68,'Thermal_or_spare'),(65,68,'Distance')]
for cx,cz,_ in front_sites:
    cut(front,(cx,-92,cz),(18,20,12))
    for dx in (-30,30):
        for dz in (-17,17): hole(front,(cx+dx,-92,cz+dz),axis='Y')
for x in (-60,-30,0,30,60): slot(front,(x,-92,25),18,4,normal='Y')
register(front,'06_replaceable_front',RX(math.pi/2),notes='Whole webcam opening; styling/lens nose can change without redesigning the cage.')

# 07: one removable electronics tray with official Pi / UNO Q hole positions.
tray=box('tray',(0,-20,55.5),(208,90,3),graphite)
for x in (-100,100):
    for y in (-55,15): hole(tray,(x,y,55.5))
pi_c=(-51,-20)
# Pi rotated 180 deg so large USB/Ethernet connectors face the left service opening.
pi_holes=[(pi_c[0]-x,pi_c[1]-y) for x in (-39,19) for y in (-24.5,24.5)]
# UNO Q drawing: coordinates from board's left/top; rotate 180 for USB-C toward right.
uno_c=(49,-20)
uno_native=[(15.24,2.54),(13.97,50.80),(66.04,17.78),(66.04,45.72)]
uno_holes=[(uno_c[0]-(x-34.29),uno_c[1]-(26.67-y)) for x,y in uno_native]
for x,y in pi_holes: hole(tray,(x,y,55.5),2.9)
for x,y in uno_holes: hole(tray,(x,y,55.5),3.4)
# Slots avoid all mounting positions.
for cx in (-51,49):
    for dx in (-16,0,16): slot(tray,(cx+dx,-20,55.5),20,5,axis='Y')
register(tray,'07_Pi4_and_UNOQ_shelf',notes='Pi: M2.5, UNO Q: M3. Separate 6 mm insulating spacers. Native board patterns, not a shared guessed rectangle.')

# 08: camera shelf and strap cradle, oversized for the intact C270 clip.
camtray=box('camera shelf',(0,-40,112.5),(208,86,3),graphite)
for x in (-100,100):
    for y in (-72,-20): hole(camtray,(x,y,112.5))
for x in (-49,49): boolean(camtray,box('camera guide',(x,-42,123),(4,70,18)),'UNION')
for x in (-43,43):
    for y in (-66,-24): slot(camtray,(x,y,112.5),16,4,axis='Y')
for x in (-84,-66,66,84): slot(camtray,(x,-42,112.5),40,5,axis='Y')
register(camtray,'08_Logitech_clip_cradle',notes='Adjust webcam on non-slip pad; strap around clip/base, never over lens or microphone. Cable exits rear/side.')

# 09: no battery enclosure, only removable low cradle with strap paths.
bank=box('bank cradle',(0,30,5.5),(160,96,3),graphite)
for x in (-76,76):
    for y in (-12,72): hole(bank,(x,y,5.5))
for y in (-9,69): boolean(bank,box('bank rail',(0,y,9.5),(144,3,5)),'UNION')
for x in (-75,75):
    for y in (0,60): boolean(bank,box('bank corner stop',(x,y,9.5),(3,12,5)),'UNION')
for x in (-45,45):
    for y in (-14,74): slot(bank,(x,y,5.5),30,4)
register(bank,'09_Anker_strap_cradle',notes='No tight clamp, no battery modification. Two straps pass through cradle AND aligned floor slots.')

# 10: universal external PCB carrier, replicated at roof/front sites.
carrier=box('sensor carrier',(0,0,1.5),(70,46,3),orange)
for x in (-30,30):
    for y in (-17,17): hole(carrier,(x,y,1.5))
for x in (-16,16):
    for y in (-8,8): slot(carrier,(x,y,1.5),10,3.4)
for x in (-25,25): slot(carrier,(x,0,1.5),20,3,axis='Y')
cut(carrier,(0,0,1.5),(14,8,30))
place(carrier,(roof_sites[0][0],roof_sites[0][1],180))
register(carrier,'10_sensor_control_carrier_PRINT_5',qty=5,notes='Four small slots suit measured Modulino-style mounting; side tie slots accommodate unverified thermal/small breakouts using insulating stand-offs.')
for x,y,name in roof_sites[1:]: clone(carrier,'carrier '+name,(x-roof_sites[0][0],y-roof_sites[0][1],0))
for x,z,name in front_sites:
    clone(carrier,'carrier '+name,matrix=Matrix.Translation((x,-94,z)) @ RX(math.pi/2))

# Separate insulating board spacers. Each STL contains one part, quantities in manifest.
def spacer(name,bore,h,loc,qty):
    o=cylinder((0,0,h/2),3,h,mat=orange); hole(o,(0,0,h/2),bore)
    place(o,loc); return register(o,name,qty=qty)
sp=spacer('11_Pi_M2p5_spacer_6mm_PRINT_4',2.9,6,(*pi_holes[0],57),4)
for x,y in pi_holes[1:]: clone(sp,'Pi spacer',(x-pi_holes[0][0],y-pi_holes[0][1],0))
sq=spacer('12_UNO_M3_spacer_6mm_PRINT_4',3.4,6,(*uno_holes[0],57),4)
for x,y in uno_holes[1:]: clone(sq,'UNO spacer',(x-uno_holes[0][0],y-uno_holes[0][1],0))
# Sensor spacer is exported but hidden; install where actual PCB holes permit.
ss=spacer('13_sensor_spacer_5mm_AS_NEEDED',3.4,5,(0,0,-30),20)
ss.hide_render=True; ss.hide_set(True); ss['optional_not_installed']=True
# Reuse the screen washer STL, made visible at screen tab locations.
bpy.ops.wm.stl_import(filepath=str(SOURCE.parent/'04_washer_PRINT_FOUR.stl'))
sw=bpy.context.object; sw.data.materials.append(orange)
place(sw,(-78,94,42.5),RX(-math.pi/2))
register(sw,'14_screen_tab_washer_PRINT_4',RX(math.pi/2),4)
for x,z in [(-78,157.5),(78,42.5),(78,157.5)]: clone(sw,'screen washer',(x+78,0,z-42.5))
# Small first-print hole sizing coupon; no heavy full-body test needed first.
coupon=box('coupon',(0,0,-15),(46,18,4),ivory)
for x,d in [(-15,2.9),(-5,3.4),(5,3.6),(15,3.8)]: hole(coupon,(x,0,-15),d)
register(coupon,'15_fastener_fit_coupon'); coupon.hide_render=True; coupon.hide_set(True)
coupon['optional_not_installed']=True

# Coloured reference envelopes; no geometry claims beyond sourced outlines.
def reference(name,loc,dims,mat,clearance=True):
    o=box('REFERENCE '+name,loc,dims,mat); o['reference_only']=True; refs.append(o)
    if clearance: hardware.append(o)
    return o
reference('Anker A110D body',(0,30,14.7),(140.7,71.7,15.4),bankmat)
reference('Pi4 board and 37mm height budget',(-51,-20,81.5),(85,56,37),pcb)
reference('UNO Q board and 25mm height budget',(49,-20,75.5),(68.58,53.34,25),pcb)
reference('C270 intact envelope VERIFY clip angle',(0,-56.68,131),(72.91,66.64,31.91),black)
reference('display rear keepout',(0,80,100),(165.1,20,124),pcb)
reference('screen glass',(0,92,100),(165.1,4,100.5),glassmat,False)
for x,y,name in roof_sites:
    panel_refs.append(reference(name+' PCB envelope',(x,y,188.8),(41,25.36,1.6),pcb))
    for dx in (-16,16):
        for dy in (-8,8):
            o=cylinder((x+dx,y+dy,185.5),3,5,mat=orange); refs.append(o); panel_refs.append(o)
for x,z,name in front_sites:
    # Generic small sensor footprint is explicitly a planning envelope.
    panel_refs.append(reference(name+' candidate PCB',(x,-102.8,z),(41,1.6,25.36),pcb))
for dx in (-10,0,10): panel_refs.append(reference('button actuator',(-52+dx,-45,192),(6.2,6.2,4.8),black,False))
lens=cylinder((-18,-90.2,131),7,1,'Y',glassmat); refs.append(lens)
mic=box('REFERENCE microphone leave clear',(20,-90.3,131),(12,.5,6),graphite); refs.append(mic)

# Actual intersections of printable parts, plus conservative hardware keepouts.
# Mating surfaces touch, not overlap. Non-installed optional parts are excluded.
def bounds(o):
    vs=[o.matrix_world@v.co for v in o.data.vertices]
    return [(min(v[i] for v in vs),max(v[i] for v in vs)) for i in range(3)]
def candidate(a,b):
    aa=bounds(a); bb=bounds(b)
    return all(min(aa[i][1],bb[i][1])-max(aa[i][0],bb[i][0])>.03 for i in range(3))
def overlap(a,b):
    c=a.copy(); c.data=a.data.copy(); sc.collection.objects.link(c)
    t=b.copy(); t.data=b.data.copy(); sc.collection.objects.link(t)
    boolean(c,t,'INTERSECT')
    bm=bmesh.new(); bm.from_mesh(c.data); v=abs(bm.calc_volume(signed=True)); bm.free()
    bpy.data.objects.remove(c,do_unlink=True); return v
active=[o for o in instances if not o.get('optional_not_installed')]
collisions=[]
for i,a in enumerate(active):
    for b in active[i+1:]:
        if candidate(a,b):
            v=overlap(a,b)
            if v>.1: collisions.append(dict(a=a.name,b=b.name,overlap_mm3=round(v,3)))
hardware_collisions=[]
for h in hardware:
    for p in active:
        if candidate(h,p):
            v=overlap(h,p)
            if v>.1: hardware_collisions.append(dict(hardware=h.name,part=p.name,overlap_mm3=round(v,3)))
(OUT/'clearance-checks.json').write_text(json.dumps(dict(
    printable_part_intersections=collisions,hardware_envelope_intersections=hardware_collisions,
    limitation='Only modeled rigid shapes checked. Cables, hardware tolerances, fastener access, thermal performance and clip pose still require real fitting.'),indent=2)+'\n')
if collisions or hardware_collisions:
    raise RuntimeError('CLEARANCE_FAILURE '+json.dumps(collisions+hardware_collisions))

# Individual STL export, validation of topology + actual output bytes/size.
report={'revision':'R01','units':'mm','printer':'HackMIT Bambu A1/P1S 256x256x256, 0.4mm, PLA; read hardware.hackmit.org/3d-prints',
        'physical_fit_tested':False,'parts':[]}
for p in parts:
    o=p['o']; bm=bmesh.new(); bm.from_mesh(o.data); bm.normal_update()
    bad=sum(not e.is_manifold for e in bm.edges); vol=abs(bm.calc_volume(signed=True))
    remaining=set(bm.verts); islands=0
    while remaining:
        todo=[remaining.pop()]; islands+=1
        while todo:
            v=todo.pop()
            for e in v.link_edges:
                nxt=e.other_vert(v)
                if nxt in remaining: remaining.remove(nxt); todo.append(nxt)
    bm.free()
    if bad or vol<=0 or islands!=1: raise RuntimeError(f'{p["name"]}: bad edges {bad}, islands {islands}, vol {vol}')
    c=o.copy(); c.data=o.data.copy(); sc.collection.objects.link(c)
    c.hide_render=False; c.hide_viewport=False; c.hide_set(False)
    bake(c); c.data.transform(p['rotation'])
    mn=[min(v.co[i] for v in c.data.vertices) for i in range(3)]
    mx=[max(v.co[i] for v in c.data.vertices) for i in range(3)]
    size=[mx[i]-mn[i] for i in range(3)]
    if max(size)>250: raise RuntimeError(f'Build volume exceeded by {p["name"]}: {size}')
    c.data.transform(Matrix.Translation((-(mn[0]+mx[0])/2,-(mn[1]+mx[1])/2,-mn[2])))
    activate(c); f=OUT/'stl'/f'{p["name"]}.stl'
    bpy.ops.wm.stl_export(filepath=str(f),export_selected_objects=True)
    raw=f.read_bytes(); n=struct.unpack_from('<I',raw,80)[0]
    if n==0 or len(raw)!=84+50*n: raise RuntimeError('Empty/invalid STL '+str(f))
    vs=[]
    for k in range(n):
        t=struct.unpack_from('<12fH',raw,84+50*k); vs.extend([t[3:6],t[6:9],t[9:12]])
    es=[max(v[i] for v in vs)-min(v[i] for v in vs) for i in range(3)]
    if any(abs(es[i]-size[i])>.002 for i in range(3)): raise RuntimeError('STL scale error '+str(f))
    report['parts'].append(dict(name=p['name'],quantity=p['qty'],dimensions_mm=[round(x,2) for x in size],
        nonmanifold_edges=bad,connected_solids=islands,stl_triangles=n,volume_mm3=round(vol,2),notes=p['notes']))
    bpy.data.objects.remove(c,do_unlink=True)
(OUT/'print-manifest.json').write_text(json.dumps(report,indent=2)+'\n')
(OUT/'mounting-coordinates.json').write_text(json.dumps(dict(
    axes='X width; front=-Y; display=+Y; Z up; mm',screen_chassis_centres=[(x,90,z) for x in (-91,91) for z in (36,164)],
    pi_PCB_centre=pi_c,pi_mount_centres_xy=pi_holes,uno_PCB_centre=uno_c,uno_mount_centres_xy=uno_holes,
    scope=['screen','Pi4B','UNO Q','Anker A110D','Logitech C270','Buttons','Light','Movement','Distance','thermal/universal sensor provision'],
    excluded=['ESP32/BOX-3','internal ASUS','printer','unverified speaker','internal TP-Link hub']),indent=2)+'\n')

# Visual assembly, cutaway and exploded view. Only real print parts go to STLs.
def text(name,body,loc,size,rotation=(0,0,0),mat=ivory):
    bpy.ops.object.text_add(location=loc,rotation=rotation); o=bpy.context.object; o.name=name
    o.data.body=body; o.data.size=size; o.data.align_x='CENTER'; o.data.extrude=.01
    o.data.materials.append(mat); refs.append(o); return o
panel_refs.append(text('Reference brand','N I M B U S',(0,-94.1,94),7,(math.pi/2,0,0),graphite))
text('Reference display caption','CAMERA  /  LIVE',(0,94.1,113),5,(math.pi/2,0,math.pi))
text('Reference display hint','CAPTURE    ENHANCE',(0,94.1,82),4,(math.pi/2,0,math.pi))
floor=box('Studio floor',(0,0,-7),(2500,2500,6),bg)
def point(o,p): o.rotation_euler=(Vector(p)-o.location).to_track_quat('-Z','Y').to_euler()
bpy.ops.object.camera_add(location=(360,-460,320)); cam=bpy.context.object
cam.data.type='ORTHO'; cam.data.ortho_scale=365; cam.data.clip_end=5000; sc.camera=cam
for xyz,power,size in [((120,-200,500),11000000,400),((-360,30,260),6500000,300),((100,360,400),9000000,350)]:
    bpy.ops.object.light_add(type='AREA',location=xyz); l=bpy.context.object
    l.data.energy=power; l.data.size=size; point(l,(0,0,90))
sc.render.engine='CYCLES'; sc.cycles.samples=24
sc.render.resolution_x=1500; sc.render.resolution_y=1200; sc.render.resolution_percentage=100
sc.world.color=(.22,.22,.22); sc.view_settings.view_transform='AgX'
point(cam,(0,0,90)); activate(screen)
for area in bpy.context.screen.areas:
    if area.type=='VIEW_3D':
        area.spaces.active.region_3d.view_distance=480
        area.spaces.active.region_3d.view_location=(0,0,90)
        area.spaces.active.clip_end=5000
bpy.ops.wm.save_as_mainfile(filepath=str(OUT/'nimbus-modular-camera.blend'))
def render(name,loc,target=(0,0,90),scale=365):
    cam.location=loc; point(cam,target); cam.data.ortho_scale=scale
    sc.render.filepath=str(OUT/name); bpy.ops.render.render(write_still=True)
render('assembled-front.png',(360,-460,320))
render('assembled-rear.png',(-360,460,300))
hidden=[roof,front,sides[1]]+panel_refs
for o in hidden: o.hide_render=True
for o in instances:
    if o.name.startswith('carrier'): o.hide_render=True
carrier.hide_render=True
render('cutaway.png',(380,-400,350),scale=375)
for o in hidden: o.hide_render=False
for o in instances:
    if o.name.startswith('carrier'): o.hide_render=False
carrier.hide_render=False
for r in refs: r.hide_render=True
saved={o:o.matrix_world.copy() for o in active}
for o in active:
    if o==screen or 'screen washer' in o.name or o==sw: o.location.y+=95
    elif o==roof or 'carrier' in o.name or o==carrier: o.location.z+=100
    elif o==front: o.location.y-=95
    elif o==sides[0]: o.location.x-=95
    elif o==sides[1]: o.location.x+=95
    elif o==camtray: o.location.z+=45
render('exploded.png',(500,-620,510),(0,0,120),650)
for o,m in saved.items(): o.matrix_world=m
for r in refs: r.hide_render=False

# Markdown print list is generated from actual exports, not hard-coded guesses.
lines=['# Print manifest — Nimbus modular camera R01','',
       'Individual STLs; millimetres; print at 100%. Fit and strength not physically tested.','',
       '| File | Copies | Print dimensions (mm) |','|---|---:|---|']
for p in report['parts']:
    lines.append(f'| {p["name"]}.stl | {p["quantity"]} | '+ ' × '.join(str(x) for x in p['dimensions_mm'])+' |')
lines+=['','Sensor spacer quantity is a maximum provision, not a requirement. Print only the sensor carriers you use.',
        'Print screen/frame and fastener coupon first; do not duplicate an already printed screen holder.']
(OUT/'PRINT_MANIFEST.md').write_text('\n'.join(lines)+'\n')
with zipfile.ZipFile(OUT/'nimbus-modular-camera-r01.zip','w',zipfile.ZIP_DEFLATED) as z:
    for p in OUT.iterdir():
        if p.suffix in ('.md','.json','.py','.blend','.png'): z.write(p,p.name)
    for p in (OUT/'stl').glob('*.stl'): z.write(p,'stl/'+p.name)
    for p in INPUTS.glob('*.stl'): z.write(p,'inputs/'+p.name)
print('MODULAR_CAMERA_BUILD_OK '+str(OUT))
