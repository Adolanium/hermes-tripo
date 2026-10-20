"""What a downloaded GLB actually contains: meshes, skins, joints, animation clips.

A task that reports success was accepted and billed; it does not prove the rig or the clip landed
in the file. tripo_download reads the GLB's JSON chunk (stdlib only, no glTF library) so the agent
reports what is really there.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

MAX_JSON_BYTES = 64 * 1024 * 1024


def summarize(path: Path) -> dict[str, Any] | None:
    """Counts and clip names for a .glb, or None when the file is not a readable GLB."""
    try:
        with path.open("rb") as handle:
            header = handle.read(20)
            if len(header) < 20:
                return None
            magic, version, _total, length, kind = struct.unpack("<4sIII4s", header)
            if magic != b"glTF" or kind != b"JSON" or not 0 < length <= MAX_JSON_BYTES:
                return None
            gltf = json.loads(handle.read(length).decode("utf-8"))
    except (OSError, ValueError, struct.error):
        return None
    if not isinstance(gltf, dict):
        return None

    skins = [skin for skin in gltf.get("skins") or [] if isinstance(skin, dict)]
    clips = [clip for clip in gltf.get("animations") or [] if isinstance(clip, dict)]
    return {
        "gltf_version": version,
        "meshes": len(gltf.get("meshes") or []),
        "nodes": len(gltf.get("nodes") or []),
        "textures": len(gltf.get("images") or []),
        "skins": len(skins),
        "joints": sum(len(skin.get("joints") or []) for skin in skins),
        "clips": [
            {"name": clip.get("name") or f"clip {index + 1}", "channels": len(clip.get("channels") or [])}
            for index, clip in enumerate(clips)
        ],
    }
