"""Compile the supported NPR outline node arithmetic into a GPU vertex shader.

No approximation of the node displacement formula: unsupported nodes fail
closed so the caller can retain the original geometry-node path.
"""
import math

VIEW_INPUTS = {
    'cam_right': 'viewData[0].xyz', 'cam_up': 'viewData[1].xyz',
    'cam_look': 'viewData[2].xyz', 'cam_pos': 'viewData[3].xyz',
    'half_fov': 'viewData[0].w', 'screen_x': 'abs(viewData[1].w)',
    'screen_y': 'viewData[2].w', 'ruri_view_valid': '1.0',
    'ruri_projection_perspective': 'float(viewData[1].w > 0.0)',
    'ruri_depth_coefficient': 'viewData[3].w',
}
ATTRIBUTES = {'UV2': ('attrUV2', 'vec3'), 'ruri_tangent': ('attrTangent', 'vec3'),
              'ruri_tangent_sign': ('attrSign', 'float'), 'UVMap': ('attrUV', 'vec3')}


def literal(value, kind='float'):
    def number(x):
        x=float(x)
        if not math.isfinite(x): raise ValueError('Non-finite outline input')
        return repr(x)
    if kind=='float': return number(value)
    count=3 if kind=='vec3' else 4
    return kind+'('+','.join(number(x) for x in list(value)[:count])+')'


def kind(socket):
    return {'VECTOR':'vec3','RGBA':'vec4'}.get(socket.type,'float')


def convert(expr, source, target):
    if source==target:return expr
    if source=='float':return target+'('+expr+')'
    if target=='float':return 'dot(('+expr+').xyz,vec3(1.0/3.0))'
    if source=='vec4' and target=='vec3':return '('+expr+').xyz'
    if source=='vec3' and target=='vec4':return 'vec4('+expr+',1.0)'
    raise ValueError((source,target))


class Compiler:
    def __init__(self, group_node, attributes=None):
        self.outer=group_node
        self.tree=group_node.node_tree
        self.lines=[]
        self.memo={}
        self.images=[]
        self.attributes=set(ATTRIBUTES if attributes is None else attributes)

    def input(self, socket, target=None):
        if socket.is_linked:
            source=socket.links[0].from_socket
            result=self.output(source)
            return convert(result,kind(source),target or kind(socket))
        return literal(socket.default_value,target or kind(socket))

    def output(self, socket):
        key=socket.as_pointer()
        if key in self.memo:return self.memo[key]
        n=socket.node;t=n.bl_idname;k=kind(socket)
        inp=lambda i,target=None:self.input(n.inputs[i],target)
        if t=='NodeGroupInput':
            name=socket.name
            expr=VIEW_INPUTS.get(name)
            if expr is None:expr=literal(self.outer.inputs[name].default_value,k)
        elif t=='GeometryNodeInputPosition':expr='position'
        elif t=='GeometryNodeInputNormal':expr='normal'
        elif t=='GeometryNodeInputNamedAttribute':
            name=n.inputs['Name'].default_value
            if name not in ATTRIBUTES:raise ValueError('Unsupported outline attribute: '+name)
            expr=('1.0' if name in self.attributes else '0.0') if socket.name=='Exists' else convert(*ATTRIBUTES[name],k)
        elif t=='ShaderNodeSeparateXYZ':expr='('+inp(0,'vec3')+').'+socket.name.lower()
        elif t=='ShaderNodeMath':
            a,b,c=(inp(i,'float') for i in range(3));op=n.operation
            binary={'ADD':f'({a}+{b})','SUBTRACT':f'({a}-{b})','MULTIPLY':f'({a}*{b})',
                    'DIVIDE':f'({b}==0.0?0.0:{a}/{b})','MAXIMUM':f'max({a},{b})','MINIMUM':f'min({a},{b})',
                    'LESS_THAN':f'float({a}<{b})','GREATER_THAN':f'float({a}>{b})',
                    'MULTIPLY_ADD':f'({a}*{b}+{c})'}
            unary={'SQRT':f'sqrt(max({a},0.0))','INVERSE_SQRT':f'({a}>0.0?inversesqrt({a}):0.0)',
                   'ABSOLUTE':f'abs({a})','SIGN':f'sign({a})','TANGENT':f'tan({a})'}
            expr=binary.get(op,unary.get(op))
            if expr is None:raise ValueError('Unsupported outline math: '+op)
            if n.use_clamp:expr=f'clamp({expr},0.0,1.0)'
        elif t=='ShaderNodeVectorMath':
            a,b=inp(0,'vec3'),inp(1,'vec3');op=n.operation
            choices={'ADD':f'({a}+{b})','SUBTRACT':f'({a}-{b})','MULTIPLY':f'({a}*{b})',
                     'DOT_PRODUCT':f'dot({a},{b})','CROSS_PRODUCT':f'cross({a},{b})',
                     'NORMALIZE':f'safe_normalize({a})'}
            expr=f'({a}*{inp(3,"float")})' if op=='SCALE' else choices.get(op)
            if expr is None:raise ValueError('Unsupported outline vector math: '+op)
        elif t=='ShaderNodeMix':
            if n.data_type=='VECTOR' and getattr(n,'factor_mode','UNIFORM')!='UNIFORM':
                raise ValueError('Unsupported non-uniform outline mix')
            factor=inp(0,'float')
            if n.clamp_factor:factor=f'clamp({factor},0.0,1.0)'
            if n.data_type=='VECTOR':a,b=inp(4,'vec3'),inp(5,'vec3')
            elif n.data_type=='FLOAT':a,b=inp(2,'float'),inp(3,'float')
            else:raise ValueError('Unsupported outline mix: '+n.data_type)
            expr=f'mix({a},{b},{factor})'
        elif t=='GeometryNodeImageTexture':
            image=n.inputs['Image'].default_value
            if image is None:raise ValueError('Outline mask image is missing')
            name='mask'+str(len(self.images));self.images.append((name,image,n.interpolation))
            uv='('+inp('Vector','vec3')+').xy'
            if n.extension=='REPEAT':uv=f'fract({uv})'
            elif n.extension=='EXTEND':uv=f'clamp({uv},vec2(0.0),vec2(1.0))'
            elif n.extension!='CLIP':raise ValueError('Unsupported outline mask extension')
            if n.interpolation=='Linear':expr=f'textureLod({name},{uv},0.0)'
            elif n.interpolation=='Closest':
                expr=f'texelFetch({name},ivec2(clamp({uv}*vec2(textureSize({name},0)),vec2(0.0),vec2(textureSize({name},0)-1))),0)'
            else:raise ValueError('Unsupported outline mask interpolation: '+n.interpolation)
            if n.extension=='CLIP':expr=f'({expr}*float(all(greaterThanEqual({uv},vec2(0.0)))&&all(lessThanEqual({uv},vec2(1.0)))))'
            if socket.name=='Alpha':expr+=' .a'
        else:raise ValueError('Unsupported outline node: '+t)
        var='v'+str(len(self.lines))
        self.lines.append(f'{k} {var} = {expr};')
        self.memo[key]=var
        return var

    def source(self):
        output=next(n for n in self.tree.nodes if n.type=='GROUP_OUTPUT' and n.is_active_output)
        offset=self.input(output.inputs['offset'],'vec3')
        return ('vec3 safe_normalize(vec3 v){float d=dot(v,v);return d>0.0?v*inversesqrt(d):vec3(0.0);}\n'
                'void main(){\n'+'\n'.join(self.lines)+'\n'
                f'gl_Position = modelViewProjection * vec4(position + {offset},1.0);\n'
                'outlineUV=fragmentUV;\n}\n')


def build(group_node, base_image, floats, colors, st, attributes=None):
    import gpu
    compiler=Compiler(group_node,attributes)
    vertex=compiler.source()
    interface=gpu.types.GPUStageInterfaceInfo('endf_outline')
    interface.smooth('VEC2','outlineUV')
    info=gpu.types.GPUShaderCreateInfo()
    for index,(name,t) in enumerate([('position','VEC3'),('normal','VEC3'),('attrUV2','VEC3'),
            ('attrTangent','VEC3'),('attrSign','FLOAT'),('attrUV','VEC3'),('fragmentUV','VEC2')]):
        info.vertex_in(index,t,name)
    info.push_constant('MAT4','modelViewProjection')
    info.push_constant('MAT4','viewData')
    info.vertex_out(interface)
    info.fragment_out(0,'VEC4','fragColor')
    textures=[]
    for index,(name,image,interpolation) in enumerate(compiler.images):
        info.sampler(index,'FLOAT_2D',name)
        textures.append((name,image))
    if base_image is not None:
        info.sampler(len(textures),'FLOAT_2D','baseMap');textures.append(('baseMap',base_image))
        sample='texture(baseMap,outlineUV*'+literal(st[:2]+[0.0],'vec3')+'.xy+'+literal(st[2:]+[0.0],'vec3')+'.xy)'
    else:sample='vec4(1.0)'
    tint=colors.get('_BaseColor',[1,1,1,1])
    brightness=float(floats.get('_OutlineColorBrightness',0.5))
    saturation=float(floats.get('_OutlineColorSaturation',1.5))
    transparent=float(floats.get('_OutlineTransparent',0.0))>0.5
    fragment=f'''void main(){{
        vec4 base={sample}*{literal(tint,'vec4')};
        float luma=dot(base.rgb,vec3(0.2126,0.7152,0.0722));
        vec3 color=max(mix(vec3(luma),base.rgb,{saturation})*{brightness},vec3(0.0));
        color=mix(12.92*color,1.055*pow(color,vec3(1.0/2.4))-0.055,step(vec3(0.0031308),color));
        fragColor=vec4(color,{'base.a' if transparent else literal(tint[3])});
        if(fragColor.a<0.001)discard;
    }}'''
    info.vertex_source(vertex);info.fragment_source(fragment)
    shader=gpu.shader.create_from_info(info)
    active=[]
    for name,image in textures:
        try:shader.uniform_from_name(name)
        except ValueError:continue
        active.append((name,image))
    return shader,active,vertex
