// Hermes Tripo: an inline 3D viewer for Hermes Desktop.
//
// The agent writes `::tripo-model{task="<task id>"}` alone on a line and this renders in the message:
// a live progress card while Tripo works, then an orbitable model (GLB, FBX, OBJ or STL) with
// auto-rotate, wireframe and animation playback. The same viewer opens local model files, and a
// library pane shows where each task came from. A status-bar chip shows the Tripo balance and what
// the focused chat has spent. Data comes from this package's own backend (dashboard/plugin_api.py)
// through ctx.rest; nothing here talks to Tripo or reads the disk.
import { host } from '@hermes/plugin-sdk'
import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js'
import { FBXLoader } from 'three/examples/jsm/loaders/FBXLoader.js'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { OBJLoader } from 'three/examples/jsm/loaders/OBJLoader.js'
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js'

// Older SDKs do not export the constant; the area id is stable.
const DIRECTIVE_AREA = 'transcript.directives'
const TASK_ID = /^(task_[A-Za-z0-9_-]{1,120}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i
const POLL_MS = 3000
const POLL_LIMIT_MS = 20 * 60 * 1000

const CSS = `
.htv{position:relative;margin:4px 0;max-width:640px;border:1px solid var(--ui-stroke-secondary,color-mix(in srgb,currentColor 14%,transparent));border-radius:12px;overflow:hidden;background:color-mix(in srgb,currentColor 4%,transparent);color:var(--ui-text-primary,inherit);font:12px/1.4 var(--font-sans,system-ui)}
.htv *{box-sizing:border-box}
.htv-stage{position:relative;height:360px;transition:height .2s ease;background:radial-gradient(ellipse 70% 80% at 50% 38%,color-mix(in srgb,var(--accent,#8a7cff) 16%,transparent),transparent 72%),linear-gradient(180deg,color-mix(in srgb,currentColor 3%,transparent),color-mix(in srgb,#000 22%,transparent))}
.htv.is-tall .htv-stage{height:560px}
.htv-stage canvas{display:block;width:100%;height:100%;outline:none;cursor:grab}
.htv-stage canvas:active{cursor:grabbing}
.htv-center{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;padding:24px;text-align:center}
.htv-muted{color:var(--ui-text-tertiary,color-mix(in srgb,currentColor 55%,transparent))}
.htv-bar{width:min(260px,70%);height:4px;border-radius:2px;background:color-mix(in srgb,currentColor 12%,transparent);overflow:hidden}
.htv-bar>i{display:block;height:100%;background:var(--accent,#7c6cff);transition:width .4s ease}
.htv-bar.is-wait>i{width:35%!important;animation:htv-slide 1.4s ease-in-out infinite}
@keyframes htv-slide{0%{transform:translateX(-100%)}100%{transform:translateX(290%)}}
.htv-preview{max-width:100%;max-height:100%;object-fit:contain}
.htv-foot{position:relative;display:flex;flex-wrap:wrap;align-items:center;gap:6px;padding:6px 8px;border-top:1px solid var(--ui-stroke-secondary,color-mix(in srgb,currentColor 12%,transparent))}
.htv-meta{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.htv-btn{border:0;background:transparent;color:inherit;font:inherit;padding:3px 7px;border-radius:6px;cursor:pointer;opacity:.75}
.htv-btn:hover{opacity:1;background:color-mix(in srgb,currentColor 9%,transparent)}
.htv-btn[aria-pressed="true"]{opacity:1;background:color-mix(in srgb,var(--accent,#7c6cff) 22%,transparent)}
.htv-select{border:1px solid color-mix(in srgb,currentColor 18%,transparent);background:transparent;color:inherit;font:inherit;border-radius:6px;padding:2px 4px;max-width:150px}
.htv-select option{color:#111}
.htv-menu{position:absolute;right:8px;bottom:calc(100% + 6px);z-index:5;min-width:260px;padding:6px;border-radius:10px;border:1px solid var(--ui-stroke-secondary,color-mix(in srgb,currentColor 16%,transparent));background:var(--card,#1d1c24);box-shadow:0 10px 30px rgba(0,0,0,.35)}
.htv-menu button{display:flex;width:100%;align-items:baseline;justify-content:space-between;gap:12px;border:0;background:transparent;color:inherit;font:inherit;text-align:left;padding:6px 8px;border-radius:6px;cursor:pointer}
.htv-menu button:hover:not(:disabled){background:color-mix(in srgb,currentColor 9%,transparent)}
.htv-menu button:disabled{opacity:.5;cursor:default}
.htv-menu small{color:var(--ui-text-tertiary,color-mix(in srgb,currentColor 55%,transparent));white-space:nowrap}
.htv-menu-head{padding:8px 8px 4px;font-size:11px;letter-spacing:.02em;color:var(--ui-text-tertiary,color-mix(in srgb,currentColor 55%,transparent))}
.htl{height:100%;min-height:0;display:flex;flex-direction:column;color:var(--ui-text-primary,inherit);font:12px/1.4 var(--font-sans,system-ui)}
.htl *{box-sizing:border-box}
.htl-head{display:flex;align-items:center;gap:8px;padding:10px 12px 8px;font-weight:600;font-size:13px}
.htl-head span{flex:1}
.htl-body{flex:1;min-height:0;overflow:auto;padding:0 10px 12px}
.htl-body .htv{margin:0 0 10px;max-width:none}
.htl-body .htv-stage{height:260px}
.htl-list{display:grid;gap:6px}
.htl-row{display:flex;align-items:center;gap:10px;width:100%;padding:6px;border:1px solid transparent;border-radius:10px;background:transparent;color:inherit;font:inherit;text-align:left;cursor:pointer}
.htl-row:hover{background:color-mix(in srgb,currentColor 6%,transparent)}
.htl-row[aria-current="true"]{border-color:color-mix(in srgb,var(--accent,#7c6cff) 55%,transparent);background:color-mix(in srgb,var(--accent,#7c6cff) 10%,transparent)}
.htl-thumb{flex:none;width:52px;height:52px;border-radius:8px;overflow:hidden;background:radial-gradient(circle at 50% 40%,color-mix(in srgb,var(--accent,#7c6cff) 18%,transparent),color-mix(in srgb,currentColor 5%,transparent));display:flex;align-items:center;justify-content:center;font-size:18px;opacity:.95}
.htl-thumb img{width:100%;height:100%;object-fit:cover}
.htl-text{min-width:0;flex:1}
.htl-text b{display:block;font-weight:500}
.htl-text small{display:block;color:var(--ui-text-tertiary,color-mix(in srgb,currentColor 55%,transparent));white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.htl-empty{padding:24px 8px;text-align:center;color:var(--ui-text-tertiary,color-mix(in srgb,currentColor 55%,transparent))}
.htv-menu hr{border:0;border-top:1px solid color-mix(in srgb,currentColor 10%,transparent);margin:6px 2px}
.htv-badge{flex:none;padding:1px 7px;border-radius:999px;font-size:11px;background:color-mix(in srgb,var(--accent,#7c6cff) 18%,transparent)}
.htv-badge.is-static{background:color-mix(in srgb,currentColor 9%,transparent)}
.htl-tabs{display:flex;gap:4px;padding:0 10px 8px}
.htl-tabs button{flex:1}
.htl-actions{display:flex;flex-wrap:wrap;gap:6px;margin:-4px 0 10px;position:relative}
.htl-made{margin:0 0 12px;padding:8px 10px;border-radius:10px;background:color-mix(in srgb,currentColor 4%,transparent)}
.htl-made q{display:block;margin:4px 0;font-style:italic;color:var(--ui-text-secondary,inherit)}
.htl-chain{display:flex;flex-wrap:wrap;align-items:center;gap:4px}
.htl-chain span{padding:1px 6px;border-radius:6px;background:color-mix(in srgb,currentColor 7%,transparent)}
.htl-chain span[aria-current="true"]{background:color-mix(in srgb,var(--accent,#7c6cff) 22%,transparent)}
.htl-open{display:flex;gap:6px;margin:0 0 10px}
.htl-open input{flex:1;min-width:0;border:1px solid color-mix(in srgb,currentColor 18%,transparent);border-radius:6px;background:transparent;color:inherit;font:inherit;padding:4px 8px}
.htc{display:inline-flex;align-items:center;gap:6px;height:100%;padding:0 8px;border:0;background:transparent;color:inherit;font:inherit;font-size:12px;cursor:pointer;opacity:.85;white-space:nowrap}
.htc:hover{opacity:1;background:color-mix(in srgb,currentColor 8%,transparent)}
.htc i{width:7px;height:7px;border-radius:50%;background:var(--accent,#7c6cff)}
.htc.is-near i{background:#e0a030}
.htc.is-over i{background:#e05050}
`

const formatBytes = n => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`)
const formatCount = n => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : String(n))

function decode(base64) {
  const text = atob(base64)
  const bytes = new Uint8Array(text.length)
  for (let i = 0; i < text.length; i++) bytes[i] = text.charCodeAt(i)
  return bytes
}

/** Tripo's answer for a task id it does not know, said in words a person can act on. */
function explain(message) {
  if (/(1004|404)|invalid task_?id|not found/i.test(message || '')) {
    return "This task isn't on your Tripo account (it may be from a test, another account, or has expired)."
  }
  return message
}

async function call(ctx, path, opts = {}) {
  const answer = await ctx.rest(path, { timeoutMs: 180000, ...opts })
  if (!answer || answer.ok !== true) throw new Error(answer?.error || 'The Tripo plugin backend did not answer')
  return answer
}

/** Parse model bytes into a scene object plus its animation clips. */
async function parseModel(format, buffer) {
  if (format === 'glb') {
    const gltf = await new GLTFLoader().parseAsync(buffer, '')
    return { object: gltf.scene, clips: gltf.animations || [] }
  }
  if (format === 'fbx') {
    const object = new FBXLoader().parse(buffer, '')
    return { object, clips: object.animations || [] }
  }
  if (format === 'obj') {
    return { object: new OBJLoader().parse(new TextDecoder().decode(buffer)), clips: [] }
  }
  if (format === 'stl') {
    const geometry = new STLLoader().parse(buffer)
    geometry.computeVertexNormals()
    const material = new THREE.MeshPhysicalMaterial({ color: 0xd9d7e6, roughness: 0.42, metalness: 0.05, clearcoat: 0.4 })
    return { object: new THREE.Mesh(geometry, material), clips: [] }
  }
  throw new Error(`Cannot show .${format} files`)
}

function countTriangles(object) {
  let total = 0
  object.traverse(node => {
    const geometry = node.isMesh && node.geometry
    if (!geometry) return
    total += geometry.index ? geometry.index.count / 3 : (geometry.attributes.position?.count || 0) / 3
  })
  return Math.round(total)
}

function disposeObject(object) {
  object.traverse(node => {
    node.geometry?.dispose?.()
    const materials = Array.isArray(node.material) ? node.material : node.material ? [node.material] : []
    for (const material of materials) {
      for (const value of Object.values(material)) if (value?.isTexture) value.dispose()
      material.dispose()
    }
  })
}

/** The app's accent color, so the stage glow matches the theme. */
function accentColor(element) {
  const value = getComputedStyle(element).getPropertyValue('--accent').trim()
  try {
    return new THREE.Color(value || '#8a7cff')
  } catch {
    return new THREE.Color('#8a7cff')
  }
}

/** A soft round glow under the model, a thin ring, and an invisible floor that only shows shadows. */
function makeStage(footprint, radius, accent) {
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = 256
  const paint = canvas.getContext('2d')
  const glow = paint.createRadialGradient(128, 128, 0, 128, 128, 128)
  const rgb = `${Math.round(accent.r * 255)},${Math.round(accent.g * 255)},${Math.round(accent.b * 255)}`
  glow.addColorStop(0, `rgba(${rgb},0.42)`)
  glow.addColorStop(0.45, `rgba(${rgb},0.14)`)
  glow.addColorStop(1, `rgba(${rgb},0)`)
  paint.fillStyle = glow
  paint.fillRect(0, 0, 256, 256)
  const texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace

  const group = new THREE.Group()
  const disc = new THREE.Mesh(
    new THREE.CircleGeometry(footprint * 3.2, 64),
    new THREE.MeshBasicMaterial({ map: texture, transparent: true, depthWrite: false, toneMapped: false })
  )
  const ring = new THREE.Mesh(
    new THREE.RingGeometry(footprint * 1.55, footprint * 1.58, 128),
    new THREE.MeshBasicMaterial({ color: accent, transparent: true, opacity: 0.35, depthWrite: false, toneMapped: false })
  )
  const floor = new THREE.Mesh(
    new THREE.PlaneGeometry(radius * 8, radius * 8),
    new THREE.ShadowMaterial({ opacity: 0.5, depthWrite: false })
  )
  for (const mesh of [disc, ring, floor]) mesh.rotation.x = -Math.PI / 2
  disc.position.y = radius * 0.001
  ring.position.y = radius * 0.002
  floor.receiveShadow = true
  group.add(floor, disc, ring)
  return {
    group,
    dispose: () => {
      for (const mesh of [disc, ring, floor]) {
        mesh.geometry.dispose()
        mesh.material.dispose()
      }
      texture.dispose()
    }
  }
}

/** The WebGL stage. Owns renderer, camera, controls and the animation mixer. */
function Stage({ model, autoRotate, wireframe, clip, playing, resetToken, onStats }) {
  const host = useRef(null)
  const scene = useRef(null)

  useEffect(() => {
    const element = host.current
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
    renderer.outputColorSpace = THREE.SRGBColorSpace
    renderer.toneMapping = THREE.ACESFilmicToneMapping
    renderer.toneMappingExposure = 1.1
    renderer.shadowMap.enabled = true
    renderer.shadowMap.type = THREE.PCFSoftShadowMap
    element.appendChild(renderer.domElement)
    const accent = accentColor(element)

    // Studio light: image-based fill, a warm key that casts the shadow, a cool rim for edges.
    const world = new THREE.Scene()
    const pmrem = new THREE.PMREMGenerator(renderer)
    const environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture
    world.environment = environment
    world.environmentIntensity = 0.5
    world.add(new THREE.HemisphereLight(0xdfe4ff, 0x1a1626, 0.25))
    const key = new THREE.DirectionalLight(0xfff1e0, 3.0)
    const rim = new THREE.DirectionalLight(0xa9bbff, 3.2)
    world.add(key, key.target, rim)

    const camera = new THREE.PerspectiveCamera(35, 1, 0.01, 1000)
    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.autoRotateSpeed = 1.2
    // In a transcript the wheel must keep scrolling the chat: zoom only with Ctrl/Cmd held. This
    // capture listener on the wrapper runs before OrbitControls' own listener on the canvas.
    const gateZoom = event => {
      controls.enableZoom = event.ctrlKey || event.metaKey
    }
    element.addEventListener('wheel', gateZoom, { capture: true, passive: true })

    // Center the model on the origin, standing on y = 0, and frame it.
    const object = model.object
    const box = new THREE.Box3().setFromObject(object)
    const size = box.getSize(new THREE.Vector3())
    const center = box.getCenter(new THREE.Vector3())
    object.position.sub(new THREE.Vector3(center.x, box.min.y, center.z))
    object.traverse(node => {
      if (node.isMesh) node.castShadow = node.receiveShadow = true
    })
    world.add(object)
    const radius = Math.max(size.length() / 2, 1e-3)

    key.position.set(radius * 1.3, radius * 4.2, radius * 1.9)
    rim.position.set(-radius * 2.6, radius * 1.8, -radius * 3)
    key.castShadow = true
    key.shadow.mapSize.set(2048, 2048)
    key.shadow.bias = -0.0004
    key.shadow.normalBias = radius * 0.01
    Object.assign(key.shadow.camera, { left: -radius * 2.4, right: radius * 2.4, top: radius * 2.4, bottom: -radius * 2.4, near: radius * 0.5, far: radius * 10 })
    key.shadow.camera.updateProjectionMatrix()

    const stage = makeStage(Math.max(size.x, size.z) * 0.5, radius, accent)
    world.add(stage.group)

    const home = () => {
      const distance = (radius / Math.sin(THREE.MathUtils.degToRad(camera.fov / 2))) * 1.08
      camera.position.set(distance * 0.6, size.y * 0.5 + distance * 0.2, distance * 0.8)
      camera.near = radius / 100
      camera.far = distance * 20
      camera.updateProjectionMatrix()
      controls.target.set(0, size.y * 0.48, 0)
      controls.update()
    }
    home()
    // Reveal: the camera eases in from further out and the model settles into place.
    const settled = camera.position.clone()
    const introStart = performance.now()
    const INTRO_MS = 1100
    camera.position.copy(controls.target).add(settled.clone().sub(controls.target).multiplyScalar(1.45))

    const mixer = model.clips.length ? new THREE.AnimationMixer(object) : null
    const clock = new THREE.Clock()
    let visible = true
    let frame = 0

    const resize = () => {
      const { clientWidth: width, clientHeight: height } = element
      if (!width || !height) return
      renderer.setSize(width, height, false)
      camera.aspect = width / height
      camera.updateProjectionMatrix()
      // Resizing clears the canvas; draw now rather than wait for the next animation frame, which a
      // background or throttled window may not deliver for a while.
      renderer.render(world, camera)
    }
    const sizer = new ResizeObserver(resize)
    sizer.observe(element)
    resize()

    // Stop drawing while scrolled out of view: a long chat can hold many viewers.
    const watcher = new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting
      if (visible && !frame) loop()
    })
    watcher.observe(element)

    const loop = () => {
      if (!visible) {
        frame = 0
        return
      }
      frame = requestAnimationFrame(loop)
      const delta = clock.getDelta()
      const t = Math.min(1, (performance.now() - introStart) / INTRO_MS)
      if (t < 1) {
        const eased = 1 - Math.pow(1 - t, 3)
        camera.position.lerpVectors(
          controls.target.clone().add(settled.clone().sub(controls.target).multiplyScalar(1.45)),
          settled,
          eased
        )
        object.scale.setScalar(0.94 + 0.06 * eased)
      } else if (object.scale.x !== 1) {
        object.scale.setScalar(1)
      }
      mixer?.update(delta)
      controls.update()
      renderer.render(world, camera)
    }
    loop()

    scene.current = { controls, mixer, object, home, actions: new Map() }
    onStats({ triangles: countTriangles(object) })

    return () => {
      cancelAnimationFrame(frame)
      visible = false
      watcher.disconnect()
      sizer.disconnect()
      element.removeEventListener('wheel', gateZoom, { capture: true })
      controls.dispose()
      mixer?.stopAllAction()
      disposeObject(object)
      stage.dispose()
      environment.dispose()
      pmrem.dispose()
      renderer.dispose()
      renderer.forceContextLoss()
      renderer.domElement.remove()
      scene.current = null
    }
  }, [model])

  useEffect(() => {
    if (scene.current) scene.current.controls.autoRotate = autoRotate
  }, [autoRotate, model])

  useEffect(() => {
    scene.current?.object.traverse(node => {
      const materials = Array.isArray(node.material) ? node.material : node.material ? [node.material] : []
      for (const material of materials) material.wireframe = wireframe
    })
  }, [wireframe, model])

  useEffect(() => {
    const current = scene.current
    if (!current?.mixer) return
    current.mixer.stopAllAction()
    const source = model.clips[clip]
    if (!source) return
    let action = current.actions.get(clip)
    if (!action) {
      action = current.mixer.clipAction(source)
      current.actions.set(clip, action)
    }
    action.reset().play()
    action.paused = !playing
  }, [clip, playing, model])

  useEffect(() => {
    if (resetToken) scene.current?.home()
  }, [resetToken])

  return <div className="htv-stage" ref={host} title="Drag to orbit · right-drag to pan · Ctrl or ⌘ + scroll to zoom" />
}

// What tripo_convert can export, and what each is for.
const FORMATS = [
  ['FBX', 'Unity, Unreal, Blender'],
  ['USDZ', 'Apple AR Quick Look'],
  ['GLTF', 'web viewers'],
  ['OBJ', 'any 3D app, no animation'],
  ['STL', '3D printing'],
  ['3MF', '3D printing, one color']
]

/** Put text in the focused chat's message box, else on the clipboard. Returns where it went. */
async function draft(ctx, text) {
  try {
    if (await host.composer.insertText(null, text, { mode: 'block' })) return 'composer'
  } catch {
    // An older host without the composer API.
  }
  return (await ctx.os?.writeClipboard?.(text)) ? 'clipboard' : null
}

/** "Rigged · 41 joints · 1 clip" or "Not rigged", from the GLB summary the backend sends. */
function rigBadge(contains) {
  if (!contains) return null
  const clips = (contains.clips || []).length
  if (!contains.joints) return { text: clips ? `Not rigged · ${clips} clip${clips > 1 ? 's' : ''}` : 'Not rigged', rigged: false }
  return { text: `Rigged · ${contains.joints} joints${clips ? ` · ${clips} clip${clips > 1 ? 's' : ''}` : ''}`, rigged: true }
}

// Viewer preferences from Settings → Plugins; asked once per card so a change shows on the next one.
async function preferences(ctx) {
  try {
    return await call(ctx, '/settings', { timeoutMs: 10000 })
  } catch {
    return {}
  }
}

function notify(kind, message, detail) {
  try {
    host.notify({ kind, message, ...(detail ? { detail } : {}) })
  } catch {
    // An older host without toasts: the button state already says what happened.
  }
}

/** Download menu: the original file for free, other formats as a request the user sends. */
function DownloadMenu({ ctx, taskId, format, label, folder }) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const box = useRef(null)

  useEffect(() => {
    if (!open) return
    const close = event => {
      if (event.type === 'keydown' ? event.key === 'Escape' : !box.current?.contains(event.target)) setOpen(false)
    }
    document.addEventListener('pointerdown', close, true)
    document.addEventListener('keydown', close, true)
    return () => {
      document.removeEventListener('pointerdown', close, true)
      document.removeEventListener('keydown', close, true)
    }
  }, [open])

  const saveOriginal = async () => {
    setBusy(true)
    try {
      const saved = await call(ctx, `/save/${taskId}`, { method: 'POST', body: { name: label || '' } })
      setOpen(false)
      notify('success', `Saved ${saved.path.split(/[\\/]/).pop()}`, saved.folder)
      ctx.os?.revealPath?.(saved.path)
    } catch (error) {
      notify('error', 'Could not save the model', error.message)
    } finally {
      setBusy(false)
    }
  }

  // Converting spends credits, so the viewer never calls Tripo itself: it drafts the request
  // and the agent runs tripo_convert behind Hermes' approval prompt once the user sends it.
  const requestConversion = async target => {
    setOpen(false)
    const text =
      `Convert Tripo task ${taskId} to ${target} with tripo_convert, then download the result ` +
      `to ${folder || 'my Downloads\\Tripo folder'}.`
    let placed = false
    try {
      placed = await host.composer.insertText(null, text, { mode: 'block' })
    } catch {
      placed = false
    }
    if (placed) {
      notify('info', `${target} request is in your message box`, 'Send it to convert. Hermes asks before spending credits.')
    } else if (await ctx.os?.writeClipboard?.(text)) {
      notify('info', 'Request copied', 'Paste it into the chat to convert.')
    } else {
      notify('error', 'Could not place the request', text)
    }
  }

  const original = (format || 'model').toUpperCase()
  return (
    <span ref={box}>
      <button type="button" className="htv-btn" aria-expanded={open} aria-haspopup="menu" onClick={() => setOpen(v => !v)}>
        Download
      </button>
      {open && (
        <div className="htv-menu" role="menu">
          <button type="button" role="menuitem" disabled={busy} onClick={saveOriginal}>
            <span>{busy ? 'Saving...' : `Original ${original}`}</span>
            <small>free · Downloads\Tripo</small>
          </button>
          <hr />
          <div className="htv-menu-head">Convert on Tripo · 5 credits, Hermes asks first</div>
          {FORMATS.filter(([name]) => name !== original).map(([name, use]) => (
            <button key={name} type="button" role="menuitem" onClick={() => requestConversion(name)}>
              <span>{name}</span>
              <small>{use}</small>
            </button>
          ))}
        </div>
      )}
    </span>
  )
}

function Progress({ label, value }) {
  const known = typeof value === 'number' && value > 0
  return (
    <div className="htv-center">
      <div>{label}</div>
      <div className={`htv-bar${known ? '' : ' is-wait'}`}>
        <i style={{ width: known ? `${Math.min(100, value)}%` : undefined }} />
      </div>
    </div>
  )
}

function TripoModel({ ctx, attrs, streaming }) {
  const taskId = typeof attrs?.task === 'string' ? attrs.task.trim() : ''
  // A local model file (the library's "On this machine"), shown without asking Tripo anything.
  const localPath = typeof attrs?.path === 'string' ? attrs.path.trim() : ''
  const valid = TASK_ID.test(taskId) || Boolean(localPath)
  const [state, setState] = useState({ phase: 'checking' })
  const [model, setModel] = useState(null)
  const [stats, setStats] = useState({})
  const [contains, setContains] = useState(null)
  const [autoRotate, setAutoRotate] = useState(true)
  const touched = useRef(false)
  const [wireframe, setWireframe] = useState(false)
  const [clip, setClip] = useState(0)
  const [playing, setPlaying] = useState(true)
  const [tall, setTall] = useState(false)
  const [resetToken, setResetToken] = useState(0)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let cancelled = false
    preferences(ctx).then(prefs => {
      if (!cancelled && !touched.current && prefs.auto_spin === false) setAutoRotate(false)
    })
    return () => {
      cancelled = true
    }
  }, [ctx])

  useEffect(() => {
    if (!valid || streaming) return
    let cancelled = false
    let timer = 0
    const started = Date.now()
    const set = next => !cancelled && setState(next)

    const showPreview = async (task, reason) => {
      if (!task.has_preview) return set({ phase: 'error', task, error: reason || 'Nothing to show for this task' })
      const { data_url } = await call(ctx, `/preview/${taskId}`)
      set({ phase: 'preview', task, image: data_url, note: reason })
    }

    const chunkPath = index =>
      localPath ? `/local/model?path=${encodeURIComponent(localPath)}&chunk=${index}` : `/model/${taskId}?chunk=${index}`

    const load = async task => {
      if (!localPath && !task.viewable) {
        const note = task.model_format ? `${task.model_format.toUpperCase()} has no 3D view here` : undefined
        return showPreview(task, note)
      }
      set({ phase: 'loading', task, loaded: 0 })
      const parts = []
      const first = await call(ctx, chunkPath(0))
      parts.push(decode(first.data))
      if (!cancelled) setContains(first.contains || null)
      for (let index = 1; index < first.chunks; index++) {
        if (cancelled) return
        set({ phase: 'loading', task, loaded: (index / first.chunks) * 100, size: first.size })
        parts.push(decode((await call(ctx, chunkPath(index))).data))
      }
      const bytes = new Uint8Array(first.size)
      let offset = 0
      for (const part of parts) {
        bytes.set(part, offset)
        offset += part.length
      }
      try {
        const parsed = await parseModel(first.format, bytes.buffer)
        if (cancelled) return disposeObject(parsed.object)
        setModel({ ...parsed, format: first.format, size: first.size })
        set({ phase: 'ready', task })
      } catch (error) {
        if (localPath) return set({ phase: 'error', task, error: `Could not open the model: ${error.message}` })
        await showPreview(task, `Could not open the model: ${error.message}`)
      }
    }

    const poll = async () => {
      if (localPath) {
        try {
          await load({})
        } catch (error) {
          set({ phase: 'error', error: error.message })
        }
        return
      }
      try {
        const task = await call(ctx, `/task/${taskId}`)
        if (cancelled) return
        if (!task.done) {
          if (Date.now() - started > POLL_LIMIT_MS) return set({ phase: 'stalled', task })
          set({ phase: 'running', task })
          timer = window.setTimeout(poll, POLL_MS)
          return
        }
        if (task.status !== 'success') {
          return set({ phase: 'error', task, error: task.error_message || `Task ${task.status}` })
        }
        await load(task)
      } catch (error) {
        set({ phase: 'error', error: explain(error.message) })
      }
    }
    poll()
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [ctx, taskId, localPath, valid, streaming, attempt])

  if (!valid) return null
  if (streaming) return <div className="htv"><Progress label="Tripo model" /></div>

  const task = state.task || {}
  const meta = [
    model && model.format.toUpperCase(),
    model && formatBytes(model.size),
    stats.triangles && `${formatCount(stats.triangles)} triangles`,
    task.credits_consumed != null && `${task.credits_consumed} credits`,
    attrs.label
  ].filter(Boolean)
  const badge = rigBadge(contains)

  let body
  if (state.phase === 'ready' && model) {
    body = (
      <Stage
        model={model}
        autoRotate={autoRotate}
        wireframe={wireframe}
        clip={clip}
        playing={playing}
        resetToken={resetToken}
        onStats={setStats}
      />
    )
  } else if (state.phase === 'preview') {
    body = (
      <div className="htv-stage">
        <div className="htv-center">
          <img className="htv-preview" src={state.image} alt="Tripo preview render" />
        </div>
      </div>
    )
  } else if (state.phase === 'error' || state.phase === 'stalled') {
    body = (
      <div className="htv-stage">
        <div className="htv-center">
          <div>{state.phase === 'stalled' ? 'Still running on Tripo.' : state.error}</div>
          <button type="button" className="htv-btn" onClick={() => setAttempt(n => n + 1)}>
            Check again
          </button>
        </div>
      </div>
    )
  } else {
    const label =
      state.phase === 'running'
        ? `${task.status === 'queued' ? 'Queued on Tripo' : 'Generating'}${task.progress ? ` · ${task.progress}%` : ''}`
        : state.phase === 'loading'
          ? `Loading model${state.size ? ` · ${formatBytes(state.size)}` : ''}`
          : 'Checking task'
    body = (
      <div className="htv-stage">
        <Progress label={label} value={state.phase === 'running' ? task.progress : state.loaded} />
      </div>
    )
  }

  const clips = model?.clips || []
  return (
    <div className={`htv${tall ? ' is-tall' : ''}`}>
      {body}
      <div className="htv-foot">
        <span className="htv-meta htv-muted" title={localPath || taskId}>
          {meta.length ? meta.join(' · ') : localPath.split(/[\\/]/).pop() || taskId}
          {state.note ? ` · ${state.note}` : ''}
        </span>
        {badge && (
          <span className={`htv-badge${badge.rigged ? '' : ' is-static'}`} title="Read from the file itself">
            {badge.text}
          </span>
        )}
        {state.phase === 'ready' && (
          <>
            {clips.length > 0 && (
              <>
                <select
                  className="htv-select"
                  value={clip}
                  aria-label="Animation"
                  onChange={event => setClip(Number(event.target.value))}
                >
                  {clips.map((item, index) => (
                    <option key={index} value={index}>
                      {item.name || `Animation ${index + 1}`}
                    </option>
                  ))}
                </select>
                <button type="button" className="htv-btn" onClick={() => setPlaying(value => !value)}>
                  {playing ? 'Pause' : 'Play'}
                </button>
              </>
            )}
            <button
              type="button"
              className="htv-btn"
              aria-pressed={autoRotate}
              onClick={() => {
                touched.current = true
                setAutoRotate(v => !v)
              }}
            >
              Spin
            </button>
            <button type="button" className="htv-btn" aria-pressed={wireframe} onClick={() => setWireframe(v => !v)}>
              Wireframe
            </button>
            <button type="button" className="htv-btn" onClick={() => setResetToken(n => n + 1)}>
              Reset
            </button>
          </>
        )}
        {!localPath && (state.phase === 'ready' || state.phase === 'preview') && task.model_format && (
          <DownloadMenu ctx={ctx} taskId={taskId} format={model?.format || task.model_format} label={attrs.label} folder={task.downloads_dir} />
        )}
        <button type="button" className="htv-btn" aria-pressed={tall} onClick={() => setTall(v => !v)}>
          {tall ? 'Smaller' : 'Bigger'}
        </button>
      </div>
    </div>
  )
}

// One thumbnail fetch at a time: each first fetch also pulls the preview from Tripo's CDN.
let thumbQueue = Promise.resolve()

function Thumb({ ctx, taskId }) {
  const box = useRef(null)
  const [src, setSrc] = useState(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    const element = box.current
    let cancelled = false
    const watcher = new IntersectionObserver(([entry]) => {
      if (!entry.isIntersecting) return
      watcher.disconnect()
      thumbQueue = thumbQueue.then(async () => {
        if (cancelled) return
        try {
          const { data_url } = await call(ctx, `/preview/${taskId}`)
          if (!cancelled) setSrc(data_url)
        } catch {
          if (!cancelled) setFailed(true)
        }
      })
    })
    watcher.observe(element)
    return () => {
      cancelled = true
      watcher.disconnect()
    }
  }, [ctx, taskId])
  return (
    <span className="htl-thumb" ref={box} aria-hidden="true">
      {src ? <img src={src} alt="" /> : failed ? '◇' : ''}
    </span>
  )
}

function when(value) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

// What a finished task can become next, drafted into the chat so the agent runs it (and Hermes
// asks before any credits are spent). `rigged` comes from the file itself when we have it.
function nextSteps(taskId, kind, rigged) {
  const steps = []
  const isModel = !/image|multiview|views|rig_?check|prerigcheck/i.test(kind || '')
  if (!isModel) {
    steps.push(['Make a 3D model from it', `Make a 3D model from Tripo task ${taskId}.`])
    return steps
  }
  if (rigged) {
    steps.push(['Add another animation', `Add another animation to Tripo task ${taskId}: `])
  } else {
    steps.push(['Rig and animate it', `Check that Tripo task ${taskId} can be rigged, then rig it and add an animation.`])
  }
  steps.push(['Retexture it', `Retexture Tripo task ${taskId}: `])
  steps.push(['Make a low-poly version', `Make a low-poly version of Tripo task ${taskId} for a game.`])
  steps.push(['Convert to FBX for Unity or Unreal', `Convert Tripo task ${taskId} to FBX and download it.`])
  steps.push(['Download it', `Download Tripo task ${taskId} into `])
  return steps
}

function ContinueMenu({ ctx, taskId, kind, rigged }) {
  const [open, setOpen] = useState(false)
  const box = useRef(null)
  useEffect(() => {
    if (!open) return
    const close = event => {
      if (event.type === 'keydown' ? event.key === 'Escape' : !box.current?.contains(event.target)) setOpen(false)
    }
    document.addEventListener('pointerdown', close, true)
    document.addEventListener('keydown', close, true)
    return () => {
      document.removeEventListener('pointerdown', close, true)
      document.removeEventListener('keydown', close, true)
    }
  }, [open])
  const pick = async text => {
    setOpen(false)
    const where = await draft(ctx, text)
    if (where === 'composer') notify('info', 'In your message box', 'Finish or send it. Hermes asks before spending credits.')
    else if (where === 'clipboard') notify('info', 'Copied', 'Paste it into a chat.')
    else notify('error', 'Could not place the request', text)
  }
  return (
    <span ref={box} style={{ position: 'relative' }}>
      <button type="button" className="htv-btn" aria-expanded={open} aria-haspopup="menu" onClick={() => setOpen(v => !v)}>
        Continue from here
      </button>
      {open && (
        <div className="htv-menu" role="menu" style={{ left: 0, right: 'auto', bottom: 'auto', top: 'calc(100% + 6px)' }}>
          {nextSteps(taskId, kind, rigged).map(([label, text]) => (
            <button key={label} type="button" role="menuitem" onClick={() => pick(text)}>
              <span>{label}</span>
            </button>
          ))}
        </div>
      )}
    </span>
  )
}

/** Where a task came from: its chat (with a way back to it), the prompt, and the steps behind it. */
function MadeFrom({ ctx, item }) {
  const [info, setInfo] = useState(null)
  useEffect(() => {
    let cancelled = false
    call(ctx, `/history/${item.task_id}`, { timeoutMs: 10000 })
      .then(answer => !cancelled && setInfo(answer))
      .catch(() => !cancelled && setInfo({ known: false }))
    return () => {
      cancelled = true
    }
  }, [ctx, item.task_id])

  const openChat = async () => {
    try {
      await host.openSession(info.session_id)
    } catch (error) {
      notify('error', 'Could not open that chat', error?.message || 'It may have been deleted.')
    }
  }

  const rigged = info?.chain?.some(step => step.tool === 'tripo_rig' || step.tool === 'tripo_animate') || /rig|retarget|animat/i.test(item.type || '')
  return (
    <>
      {info?.known && (
        <div className="htl-made">
          <div className="htl-chain">
            {info.chain.map((step, index) => (
              <span key={step.task_id} aria-current={step.task_id === item.task_id}>
                {index > 0 ? '→ ' : ''}
                {step.label}
              </span>
            ))}
          </div>
          {info.prompt && <q>{info.prompt}</q>}
          <small className="htv-muted">
            {info.session_id ? 'Made in a Hermes chat' : 'Made with Hermes'}
            {info.made_at ? ` · ${when(info.made_at)}` : ''}
          </small>
        </div>
      )}
      <div className="htl-actions">
        {info?.session_id && typeof host.openSession === 'function' && (
          <button type="button" className="htv-btn" onClick={openChat}>
            Open chat
          </button>
        )}
        <ContinueMenu ctx={ctx} taskId={item.task_id} kind={item.type} rigged={rigged} />
        {(info?.files || []).slice(-1).map(path => (
          <button key={path} type="button" className="htv-btn" onClick={() => ctx.os?.revealPath?.(path)}>
            Show file
          </button>
        ))}
      </div>
    </>
  )
}

/** Model files on this machine, and any model file you point at, in the same viewer. */
function LocalModels({ ctx }) {
  const [state, setState] = useState({ loading: true, items: [] })
  const [selected, setSelected] = useState(null)
  const [typed, setTyped] = useState('')
  const [token, setToken] = useState(0)

  useEffect(() => {
    let cancelled = false
    setState(current => ({ ...current, loading: true, error: null }))
    call(ctx, '/local', { timeoutMs: 30000 })
      .then(answer => !cancelled && setState({ loading: false, items: answer.items || [] }))
      .catch(error => !cancelled && setState({ loading: false, items: [], error: error.message }))
    return () => {
      cancelled = true
    }
  }, [ctx, token])

  const open = event => {
    event.preventDefault()
    const path = typed.trim().replace(/^["']|["']$/g, '')
    if (path) setSelected(path)
  }

  const ask = async path => {
    const where = await draft(ctx, `The 3D model at ${path}: `)
    if (where === 'clipboard') notify('info', 'Path copied', 'Paste it into the chat.')
  }

  return (
    <>
      <form className="htl-open" onSubmit={open}>
        <input
          value={typed}
          onChange={event => setTyped(event.target.value)}
          placeholder="Paste the path of a .glb, .fbx, .obj or .stl"
          aria-label="Model file path"
        />
        <button type="submit" className="htv-btn">
          Open
        </button>
      </form>
      {selected && (
        <>
          <TripoModel key={selected} ctx={ctx} attrs={{ path: selected }} streaming={false} />
          <div className="htl-actions">
            <button type="button" className="htv-btn" onClick={() => ctx.os?.revealPath?.(selected)}>
              Show file
            </button>
            <button type="button" className="htv-btn" onClick={() => ask(selected)}>
              Ask about it
            </button>
            <button type="button" className="htv-btn" onClick={() => setSelected(null)}>
              Hide viewer
            </button>
          </div>
        </>
      )}
      {state.error ? (
        <div className="htl-empty">{state.error}</div>
      ) : !state.loading && !state.items.length ? (
        <div className="htl-empty">No model files saved yet. Downloads from Tripo tasks show up here.</div>
      ) : (
        <div className="htl-list">
          {state.items.map(item => (
            <button
              key={item.path}
              type="button"
              className="htl-row"
              aria-current={item.path === selected}
              onClick={() => setSelected(item.path)}
              title={item.path}
            >
              {item.task_id ? <Thumb ctx={ctx} taskId={item.task_id} /> : <span className="htl-thumb" aria-hidden="true">◇</span>}
              <span className="htl-text">
                <b>{item.name}</b>
                <small>{[formatBytes(item.bytes), when(item.modified * 1000)].filter(Boolean).join(' · ')}</small>
              </span>
            </button>
          ))}
        </div>
      )}
      <div style={{ textAlign: 'center', marginTop: 8 }}>
        <button type="button" className="htv-btn" onClick={() => setToken(n => n + 1)} disabled={state.loading}>
          {state.loading ? 'Looking...' : 'Look again'}
        </button>
      </div>
    </>
  )
}

/** Recent Tripo tasks on this account, and model files on this machine; pick one to open it. */
function LibraryPane({ ctx, initialTab = 'account' }) {
  const [tab, setTab] = useState(initialTab)
  const [state, setState] = useState({ loading: true, items: [] })
  const [selected, setSelected] = useState(null)
  const [token, setToken] = useState(0)

  useEffect(() => {
    if (tab !== 'account') return
    let cancelled = false
    setState(current => ({ ...current, loading: true, error: null }))
    call(ctx, '/library')
      .then(answer => !cancelled && setState({ loading: false, items: answer.items || [], total: answer.total }))
      .catch(error => !cancelled && setState({ loading: false, items: [], error: explain(error.message) }))
    return () => {
      cancelled = true
    }
  }, [ctx, token, tab])

  const ask = async item => {
    const where = await draft(ctx, `Tripo task ${item.task_id} (${item.label}): `)
    if (where === 'clipboard') notify('info', 'Task reference copied', 'Paste it into the chat.')
  }

  const current = state.items.find(item => item.task_id === selected)
  return (
    <div className="htl">
      <div className="htl-head">
        <span>Tripo library</span>
        {tab === 'account' && (
          <button type="button" className="htv-btn" onClick={() => setToken(n => n + 1)} disabled={state.loading}>
            {state.loading ? 'Loading...' : 'Refresh'}
          </button>
        )}
      </div>
      <div className="htl-tabs" role="tablist">
        <button type="button" role="tab" className="htv-btn" aria-pressed={tab === 'account'} onClick={() => setTab('account')}>
          Tripo account
        </button>
        <button type="button" role="tab" className="htv-btn" aria-pressed={tab === 'local'} onClick={() => setTab('local')}>
          On this machine
        </button>
      </div>
      <div className="htl-body">
        {tab === 'local' ? (
          <LocalModels ctx={ctx} />
        ) : (
          <>
            {current && (
              <>
                <TripoModel key={current.task_id} ctx={ctx} attrs={{ task: current.task_id, label: current.label }} streaming={false} />
                <MadeFrom key={`made-${current.task_id}`} ctx={ctx} item={current} />
                <div className="htl-actions">
                  <button type="button" className="htv-btn" onClick={() => ask(current)}>
                    Ask about it
                  </button>
                  <button type="button" className="htv-btn" onClick={() => setSelected(null)}>
                    Hide viewer
                  </button>
                </div>
              </>
            )}
            {state.error ? (
              <div className="htl-empty">{state.error}</div>
            ) : !state.loading && !state.items.length ? (
              <div className="htl-empty">No Tripo tasks on this account yet.</div>
            ) : (
              <div className="htl-list">
                {state.items.map(item => (
                  <button
                    key={item.task_id}
                    type="button"
                    className="htl-row"
                    aria-current={item.task_id === selected}
                    onClick={() => setSelected(item.task_id)}
                  >
                    <Thumb ctx={ctx} taskId={item.task_id} />
                    <span className="htl-text">
                      <b>{item.label}</b>
                      <small>
                        {[when(item.created_at), item.credits != null && `${item.credits} credits`, item.from_chat && 'from a chat']
                          .filter(Boolean)
                          .join(' · ')}
                      </small>
                    </span>
                  </button>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

const CHIP_REFRESH_MS = 120000

/** Status-bar chip: the Tripo balance, and what the focused chat has spent against its limit. */
function CreditChip({ ctx, onOpen }) {
  const [state, setState] = useState(null)
  useEffect(() => {
    let alive = true
    let busy = false
    const refresh = async () => {
      if (busy) return
      busy = true
      try {
        const session = host.state?.focusedStoredSessionId?.get?.() || host.state?.activeSessionId?.get?.() || ''
        const [money, spend] = await Promise.all([
          call(ctx, '/balance', { timeoutMs: 20000 }),
          call(ctx, `/spend?session=${encodeURIComponent(session)}`, { timeoutMs: 10000 }).catch(() => ({}))
        ])
        if (alive) setState({ ...money, ...spend })
      } catch {
        if (alive) setState(null) // no key yet, or offline: show nothing rather than a broken chip
      } finally {
        busy = false
      }
    }
    refresh()
    const stops = [
      ctx.onEvent?.('tool.complete', event => {
        if (String(event?.payload?.name || '').startsWith('tripo_')) refresh()
      }),
      ctx.setInterval?.(refresh, CHIP_REFRESH_MS),
      host.state?.focusedStoredSessionId?.listen?.(() => refresh())
    ]
    return () => {
      alive = false
      for (const stop of stops) if (typeof stop === 'function') stop()
    }
  }, [ctx])

  if (!state || state.available == null) return null
  const spent = state.session_spent || 0
  const limit = state.session_limit || 0
  const level = limit && spent > limit ? ' is-over' : limit && spent >= limit * 0.8 ? ' is-near' : ''
  const title = [
    `Tripo: ${state.available} credits available${state.frozen ? `, ${state.frozen} held by running tasks` : ''}.`,
    spent ? `This chat: about ${spent}${limit ? ` of your ${limit}-credit limit` : ''}.` : 'This chat has not spent Tripo credits.',
    'Click to open the Tripo library.'
  ].join(' ')
  return (
    <button type="button" className={`htc${level}`} title={title} onClick={onOpen}>
      <i aria-hidden="true" />
      <span>
        Tripo {state.available}
        {spent ? ` · chat ${spent}` : ''}
      </span>
    </button>
  )
}

export default {
  id: 'hermes-tripo',
  name: 'Hermes Tripo',
  register(ctx) {
    const style = document.createElement('style')
    style.dataset.hermesTripo = ''
    style.textContent = CSS
    document.head.appendChild(style)
    ctx.onDispose(() => style.remove())

    ctx.register({
      id: 'model-viewer',
      area: DIRECTIVE_AREA,
      data: {
        name: 'tripo-model',
        render: ({ attrs, streaming }) => <TripoModel ctx={ctx} attrs={attrs} streaming={streaming} />
      }
    })

    ctx.register({
      id: 'library',
      area: 'panes',
      title: 'Tripo',
      data: { placement: 'right', width: '380px' },
      render: () => <LibraryPane ctx={ctx} />
    })
    const openLibrary = (tab = 'account') => {
      if (typeof host.openWorkspace === 'function') {
        host.openWorkspace(`hermes-tripo:library:${tab}`, { title: 'Tripo', render: () => <LibraryPane ctx={ctx} initialTab={tab} /> })
      }
    }
    ctx.register({
      id: 'open-library',
      area: 'palette',
      data: {
        id: 'hermes-tripo.library',
        label: 'Open Tripo library',
        keywords: ['tripo', '3d', 'models', 'library'],
        run: () => openLibrary('account')
      }
    })
    ctx.register({
      id: 'inspect-local',
      area: 'palette',
      data: {
        id: 'hermes-tripo.inspect',
        label: 'Tripo: inspect a local 3D model',
        keywords: ['tripo', 'glb', 'fbx', 'obj', 'stl', 'model', 'rig', 'inspect', 'view'],
        run: () => openLibrary('local')
      }
    })
    ctx.register({
      id: 'credits',
      area: 'statusBar.right',
      order: 40,
      render: () => <CreditChip ctx={ctx} onOpen={() => openLibrary('account')} />
    })
  }
}
