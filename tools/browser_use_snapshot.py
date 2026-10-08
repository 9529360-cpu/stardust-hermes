"""Self-contained structured snapshot/ref helpers injected into Browser Use exec.

The Browser Use CLI runs model code in its own interpreter, so this preamble must
not import Stardust modules or depend on the host Python environment. It uses only
the documented raw-CDP helper exposed by the CLI and fails closed when a reference
is stale or cannot be revalidated against the current document.
"""

STRUCTURED_SNAPSHOT_CONTRACT = "stardust.browser-use.snapshot.v1"

STRUCTURED_SNAPSHOT_PREAMBLE = r'''
# hermes: structured accessibility refs, scoped to the current document
import base64 as _hermes_b64
import hashlib as _hermes_hash
import json as _hermes_json

_HERMES_AX_CONTRACT = "stardust.browser-use.snapshot.v1"
_HERMES_AX_ROLES = frozenset({
    "button", "link", "textbox", "checkbox", "radio", "combobox", "searchbox",
    "tab", "menuitem", "option", "switch", "slider", "spinbutton",
})


def _hermes_ax_value(field):
    if not isinstance(field, dict):
        return None
    value = field.get("value")
    return value.get("value") if isinstance(value, dict) else value


def _hermes_frame():
    response = cdp("Page.getFrameTree")
    frame = ((response or {}).get("frameTree") or {}).get("frame") or {}
    frame_id, loader_id = frame.get("id"), frame.get("loaderId")
    if not frame_id or not loader_id:
        raise RuntimeError("Structured browser refs require a CDP frame with a loader id.")
    return frame_id, loader_id, frame.get("url", "")


def _hermes_ax_nodes(frame_id):
    response = cdp("Accessibility.getFullAXTree", frameId=frame_id)
    nodes = (response or {}).get("nodes")
    if not isinstance(nodes, list):
        raise RuntimeError("This browser backend did not return an Accessibility tree.")
    return [node for node in nodes
            if isinstance(node, dict) and (not node.get("frameId") or node.get("frameId") == frame_id)]


def _hermes_name_hash(name):
    return _hermes_hash.sha256(name.encode("utf-8")).hexdigest()[:16]


def _hermes_make_ref(node_id, frame_id, loader_id, role, name):
    payload = {"v": 1, "node": node_id, "frame": frame_id, "loader": loader_id,
               "role": role, "name": _hermes_name_hash(name)}
    encoded = _hermes_b64.urlsafe_b64encode(
        _hermes_json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    return "@ax1." + encoded


def browser_snapshot_refs(max_items=200):
    """Return actionable top-frame AX nodes and opaque, navigation-scoped refs."""
    if type(max_items) is not int or not 1 <= max_items <= 1000:
        raise ValueError("max_items must be an integer from 1 to 1000")
    frame_id, loader_id, url = _hermes_frame()
    nodes = _hermes_ax_nodes(frame_id)
    elements, seen = [], set()
    for node in nodes:
        node_id = node.get("backendDOMNodeId")
        role = _hermes_ax_value(node.get("role"))
        name_value = _hermes_ax_value(node.get("name"))
        name = name_value if isinstance(name_value, str) else str(name_value or "")
        properties = {
            item.get("name"): _hermes_ax_value(item.get("value"))
            for item in node.get("properties", []) if isinstance(item, dict)
        }
        focusable = properties.get("focusable") is True
        if (node.get("ignored") or not isinstance(node_id, int) or node_id in seen
                or not role or (role not in _HERMES_AX_ROLES and not focusable)):
            continue
        seen.add(node_id)
        disabled_value = properties.get("disabled")
        disabled = disabled_value is True or (isinstance(disabled_value, str)
                                               and disabled_value.lower() == "true")
        elements.append({
            "ref": _hermes_make_ref(node_id, frame_id, loader_id, role, name),
            "role": role,
            "name": name,
            "disabled": disabled,
        })
    return {
        "contract": _HERMES_AX_CONTRACT,
        "url": url,
        "elements": elements[:max_items],
        "total_elements": len(elements),
        "truncated": len(elements) > max_items,
    }


def _hermes_resolve_ref(ref):
    if not isinstance(ref, str) or not ref.startswith("@ax1."):
        raise ValueError("Invalid browser ref; use a ref from browser_snapshot_refs().")
    encoded = ref[5:]
    try:
        payload = _hermes_json.loads(_hermes_b64.urlsafe_b64decode(
            encoded + "=" * (-len(encoded) % 4)
        ).decode("utf-8"))
    except Exception as exc:
        raise ValueError("Invalid browser ref; use a ref from browser_snapshot_refs().") from exc
    if (not isinstance(payload, dict) or payload.get("v") != 1
            or type(payload.get("node")) is not int or not payload.get("frame")
            or not payload.get("loader") or not payload.get("role")
            or not payload.get("name")):
        raise ValueError("Invalid browser ref; use a ref from browser_snapshot_refs().")
    frame_id, loader_id, _ = _hermes_frame()
    if payload["frame"] != frame_id or payload["loader"] != loader_id:
        raise RuntimeError("Stale browser ref: the page navigated; call browser_snapshot_refs() again.")
    for node in _hermes_ax_nodes(frame_id):
        if node.get("backendDOMNodeId") != payload["node"] or node.get("ignored"):
            continue
        role = _hermes_ax_value(node.get("role"))
        name_value = _hermes_ax_value(node.get("name"))
        name = name_value if isinstance(name_value, str) else str(name_value or "")
        if role == payload["role"] and _hermes_name_hash(name) == payload["name"]:
            return payload["node"]
        break
    raise RuntimeError("Stale browser ref: the element changed; call browser_snapshot_refs() again.")


def _hermes_call_ref(ref, function, arguments=None):
    backend_node_id = _hermes_resolve_ref(ref)
    resolved = cdp("DOM.resolveNode", backendNodeId=backend_node_id)
    object_id = ((resolved or {}).get("object") or {}).get("objectId")
    if not object_id:
        raise RuntimeError("Browser ref could not be resolved to a live DOM element.")
    try:
        response = cdp("Runtime.callFunctionOn", objectId=object_id,
                       functionDeclaration=function, arguments=arguments or [], returnByValue=True)
        if (response or {}).get("exceptionDetails"):
            raise RuntimeError("Browser ref action raised a JavaScript exception.")
        value = ((response or {}).get("result") or {}).get("value")
        if not isinstance(value, dict) or value.get("success") is not True:
            message = value.get("error") if isinstance(value, dict) else None
            raise RuntimeError(message or "Browser ref action did not complete.")
        return value
    finally:
        cdp("Runtime.releaseObject", objectId=object_id)


def browser_click_ref(ref):
    """Click a current visible, enabled element returned by browser_snapshot_refs()."""
    function = """function() {
        if (!this.isConnected) return {success:false,error:'element is detached'};
        if (this.disabled || this.getAttribute('aria-disabled') === 'true')
            return {success:false,error:'element is disabled'};
        const style = getComputedStyle(this);
        if (!this.getClientRects().length || style.display === 'none' || style.visibility === 'hidden')
            return {success:false,error:'element is not visible'};
        this.click();
        return {success:true};
    }"""
    _hermes_call_ref(ref, function)
    return {"success": True, "action": "click", "ref": ref}


def browser_fill_ref(ref, text):
    """Fill a text input/textarea/contenteditable without echoing its value."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    function = """function(value) {
        if (!this.isConnected) return {success:false,error:'element is detached'};
        if (this.disabled || this.readOnly || this.getAttribute('aria-disabled') === 'true')
            return {success:false,error:'element is disabled or read-only'};
        const style = getComputedStyle(this);
        if (!this.getClientRects().length || style.display === 'none' || style.visibility === 'hidden')
            return {success:false,error:'element is not visible'};
        const tag = this.localName;
        if (this.isContentEditable) {
            this.focus();
            this.textContent = value;
        } else if (tag === 'input' || tag === 'textarea') {
            if (tag === 'input' && ['button','submit','reset','checkbox','radio','file','image'].includes(this.type))
                return {success:false,error:'element is not a text input'};
            this.focus();
            const proto = tag === 'textarea' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
            const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
            setter.call(this, '');
            this.dispatchEvent(new Event('input', {bubbles:true, composed:true}));
            setter.call(this, value);
        } else {
            return {success:false,error:'element is not an editable text field'};
        }
        this.dispatchEvent(new Event('input', {bubbles:true, composed:true}));
        this.dispatchEvent(new Event('change', {bubbles:true}));
        return {success:true,length:value.length};
    }"""
    value = _hermes_call_ref(ref, function, [{"value": text}])
    return {"success": True, "action": "fill", "ref": ref, "value_length": value.get("length", 0)}
'''
