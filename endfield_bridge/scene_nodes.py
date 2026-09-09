"""Structured scene-node contract; coordinates already converted by Sora-Core."""
import math


def validate_nodes(document):
    nodes = document.get('nodes') or []
    identities = set()
    for index, node in enumerate(nodes):
        identity = node.get('id')
        if not isinstance(identity, str) or not identity or identity in identities:
            raise ValueError('Scene node identity is absent or duplicated')
        identities.add(identity)
        parent = node.get('parent')
        if type(parent) is not int or not -1 <= parent < index:
            raise ValueError('Scene nodes require an earlier parent or -1 root')
        matrix = node.get('localMatrix')
        if (not isinstance(matrix, list) or len(matrix) != 16
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in matrix)
                or any(abs(matrix[12 + i] - value) > 1e-6 for i, value in enumerate((0, 0, 0, 1)))):
            raise ValueError('Scene node matrix must be a finite affine 4x4 matrix')
    for mesh in document['meshes']:
        node = mesh.get('node')
        space = mesh.get('coordinateSpace')
        if node is None and space is None:
            continue  # Existing v1/v2 scenes have scene-space vertices.
        if type(node) is not int or not 0 <= node < len(nodes) or space not in {'node', 'scene'}:
            raise ValueError('Mesh node or coordinate space is invalid')
        if space == 'node' and mesh.get('weights'):
            raise ValueError('Node-local skinned vertices are not part of the scene contract')
    return nodes


def create_nodes_steps(collection, token, records, created):
    import bpy
    from mathutils import Matrix
    objects = []
    for index, source in enumerate(records):
        if index % 64 == 0:
            yield {'stage': 'Creating native hierarchy', 'completed': index, 'total': len(records)}
        obj = bpy.data.objects.new(source['name'], None)
        created.append(obj)
        collection.objects.link(obj)
        obj['sora_instance'] = token
        obj['sora_node_id'] = source['id']
        obj['sora_source_path'] = source.get('sourcePath') or ''
        obj.empty_display_type = 'PLAIN_AXES'
        obj.empty_display_size = 0.03
        if source['parent'] >= 0:
            obj.parent = objects[source['parent']]
        matrix = source['localMatrix']
        obj.matrix_basis = Matrix([matrix[row*4:row*4+4] for row in range(4)])
        objects.append(obj)
    return objects


def bind_mesh_node(obj, source, nodes, shader_frame):
    from mathutils import Matrix
    index = source.get('node')
    if index is None:
        return
    obj['sora_source_node'] = nodes[index]['sora_node_id']
    obj['sora_coordinate_space'] = source['coordinateSpace']
    if source['coordinateSpace'] == 'node':
        obj.parent = nodes[index]
        obj.matrix_parent_inverse = Matrix.Identity(4)
        # object_frame rotates vertex-local p to R*p. Keep native nodes W;
        # local compensation R^-1 (=R) gives W*R*R*p = W*p exactly once.
        obj.matrix_basis = Matrix.Diagonal((-1.0, -1.0, 1.0, 1.0)) if shader_frame else Matrix.Identity(4)
