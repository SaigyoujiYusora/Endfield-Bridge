"""Avoid repeating an already completed projection-table remap.

The attributed vendor resolver remains authoritative for creation, migration,
and linked-user diagnostics. It calls ID.user_remap even for unused duplicates;
that traverses Blender IDs on every table lookup. An unused/fake-user-only
duplicate has no consumer to redirect, so the exact existing valid local table
can be returned without another remap. No datablock is removed or renamed.
"""


def resolve(runtime, original, name, width, height):
    import bpy
    base = runtime._table_base_name(name)
    image = None
    duplicates = []
    for candidate in bpy.data.images:
        if runtime._table_base_name(candidate.name) != base:
            continue
        duplicates.append(candidate)
        if (image is None and candidate.library is None
                and tuple(candidate.size) == (width, height) and candidate.is_float):
            image = candidate
    if image is not None:
        # A fake user keeps an ID in the file without a material/node consumer.
        # Be conservative about every other kind of user, including extra users.
        if all(other == image or other.users <= int(other.use_fake_user)
                for other in duplicates):
            return image
    return original(name, width, height)


def install(runtime):
    current = runtime._projection_table_image
    original = getattr(current, '_endf_projection_original', current)

    def projection_table_image(name, width, height):
        return resolve(runtime, original, name, width, height)

    projection_table_image._endf_projection_original = original
    runtime._projection_table_image = projection_table_image
