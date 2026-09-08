"""Run once via visible Blender MCP before frontend regression fixtures.
Reload consumers after dependencies, then restore callbacks for existing imports.
"""
import importlib
import json
from pathlib import Path
import endfield_bridge
from endfield_bridge import materials, ruri_adapter, scene

materials = importlib.reload(materials)
ruri_adapter = importlib.reload(ruri_adapter)
scene = importlib.reload(scene)
ruri_adapter.register()
ruri_adapter.restore()
assert scene.build_material is materials.build_material
assert scene.load_images is materials.load_images
print('FRONTEND_RELOADED', json.dumps({module.__name__: str(Path(module.__file__).resolve())
      for module in (materials, ruri_adapter, scene)}))
