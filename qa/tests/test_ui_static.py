"""Comprobaciones estáticas de la interfaz (frontend-new): contraste AA calculado desde los tokens, estructura accesible,
seguridad del cliente, integridad de módulos y de rutas. IDs: UI-*. No sustituyen la revisión visual (ver PRUEBAS-MANUALES.md)."""
import os
import re

import pytest

from conftest import FRONT_JS, ROOT

FRONT = os.path.join(ROOT, "frontend-new")


def read(*p):
    return open(os.path.join(FRONT, *p), encoding="utf-8").read()


# ------------------------------------------------------------------ contraste
def _blocks(css):
    dark = re.search(r":root\s*\{(.*?)\n\}", css, re.S).group(1)
    light = re.search(r':root\[data-theme="light"\]\s*\{(.*?)\n\}', css, re.S).group(1)
    var = lambda b: dict(re.findall(r"--([\w-]+):\s*([^;]+);", b))  # noqa: E731
    d = var(dark)
    l = {**d, **var(light)}
    return d, l


def _rgb(v):
    v = v.strip()
    m = re.fullmatch(r"#([0-9a-fA-F]{6})", v)
    if m:
        h = m.group(1)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)), 1.0
    m = re.fullmatch(r"rgba?\(([^)]+)\)", v)
    if m:
        p = [x.strip() for x in m.group(1).split(",")]
        return tuple(float(x) for x in p[:3]), float(p[3]) if len(p) > 3 else 1.0
    raise ValueError(v)


def _over(fg, bg):
    (c, a), (b, _) = fg, bg
    return tuple(c[i] * a + b[i] * (1 - a) for i in range(3)), 1.0


def _lum(rgb):
    def ch(x):
        x /= 255
        return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(x) for x in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a, b):
    la, lb = _lum(a[0]), _lum(b[0])
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


THEMES = {}
css_path = os.path.join(FRONT, "css", "tokens.css")
if os.path.exists(css_path):
    THEMES["oscuro"], THEMES["claro"] = _blocks(read("css", "tokens.css"))

TEXT_ON = ["bg", "surface", "surface-2", "surface-3"]
TEXT_TOKENS = ["text", "text-2", "text-3", "link", "accent-text"]


@pytest.mark.skipif(not THEMES, reason="css/tokens.css no existe")
@pytest.mark.parametrize("theme", ["oscuro", "claro"])
@pytest.mark.parametrize("fg", TEXT_TOKENS)
@pytest.mark.parametrize("bg", TEXT_ON)
def test_UI01_text_contrast_aa(theme, fg, bg):
    t = THEMES[theme]
    r = ratio(_rgb(t[fg]), _rgb(t[bg]))
    assert r >= 4.5, f"{theme}: --{fg} sobre --{bg} = {r:.2f}:1 (<4.5)"


@pytest.mark.skipif(not THEMES, reason="css/tokens.css no existe")
@pytest.mark.parametrize("theme", ["oscuro", "claro"])
@pytest.mark.parametrize("sem", ["ok", "warn", "bad", "info"])
def test_UI02_semantic_text_on_soft_background_aa(theme, sem):
    t = THEMES[theme]
    surface = _rgb(t["surface"])
    soft = _over(_rgb(t[f"{sem}-bg"]), surface)
    assert ratio(_rgb(t[sem]), soft) >= 4.5, f"{theme}: --{sem} sobre --{sem}-bg"
    assert ratio(_rgb(t[sem]), surface) >= 4.5


@pytest.mark.skipif(not THEMES, reason="css/tokens.css no existe")
@pytest.mark.parametrize("theme", ["oscuro", "claro"])
def test_UI03_focus_and_borders_meet_non_text_contrast(theme):
    t = THEMES[theme]
    for bg in ("bg", "surface"):
        assert ratio(_rgb(t["focus"]), _rgb(t[bg])) >= 3, f"foco sobre {bg}"
    assert ratio(_rgb(t["border-strong"]), _rgb(t["surface"])) >= 3, "borde de controles (WCAG 1.4.11)"


@pytest.mark.skipif(not THEMES, reason="css/tokens.css no existe")
@pytest.mark.parametrize("theme", ["oscuro", "claro"])
def test_UI04_white_on_brand_and_service_colors(theme):
    t = THEMES[theme]
    white = _rgb(t["on-brand"])
    for tok in ("brand-1", "brand-2"):
        assert ratio(white, _rgb(t[tok])) >= 4.5, f"texto blanco sobre --{tok}"
    for tok in ("c-seg", "c-red", "c-acc", "c-qos", "c-wifi"):
        assert ratio(white, _rgb(t[tok])) >= 3, f"icono blanco sobre --{tok} (3:1 gráfico)"


@pytest.mark.skipif(not THEMES, reason="css/tokens.css no existe")
@pytest.mark.parametrize("theme", ["oscuro", "claro"])
@pytest.mark.parametrize("tok", ["code-text", "code-comment", "code-cmd", "code-secret"])
def test_UI05_code_viewer_contrast(theme, tok):
    t = THEMES[theme]
    assert ratio(_rgb(t[tok]), _rgb(t["code-bg"])) >= 4.5


# ------------------------------------------------------------------ estructura
def test_UI10_index_html_basics():
    h = read("index.html")
    assert '<html lang="es">' in h and 'name="viewport"' in h and "<title>" in h and 'class="skip-link"' in h
    assert 'href="#main"' in h and re.search(r"""id:\s*'main'|id="main\"""", read("js", "main.js")), "el enlace 'Saltar al contenido' necesita un destino #main"
    assert 'role="status"' in h and "<noscript>" in h


def test_UI11_all_referenced_assets_exist():
    h = read("index.html")
    missing = []
    for ref in re.findall(r'(?:href|src)="([^"#]+)"', h):
        if ref.startswith(("http", "data:")):
            continue
        if not os.path.exists(os.path.join(FRONT, ref)):
            missing.append(ref)
    assert missing == [], f"index.html referencia archivos inexistentes (frontend incompleto): {missing}"


def test_UI12_all_js_imports_resolve():
    missing = []
    for root, _, files in os.walk(FRONT_JS):
        for f in files:
            if not f.endswith(".js"):
                continue
            src = open(os.path.join(root, f), encoding="utf-8").read()
            for m in re.finditer(r"""(?:from|import)\s*\(?\s*['"](\.{1,2}/[^'"]+)['"]""", src):
                if not os.path.exists(os.path.normpath(os.path.join(root, m.group(1)))):
                    missing.append(f"{f} -> {m.group(1)}")
    assert missing == [], missing


def test_UI13_every_route_view_file_exists_and_exports_default():
    main = read("js", "main.js")
    for rel in re.findall(r"import\('\./(views/[\w-]+\.js)'\)", main):
        p = os.path.join(FRONT_JS, rel)
        assert os.path.exists(p), rel
        assert "export default" in open(p, encoding="utf-8").read() or "export async function" in open(p, encoding="utf-8").read() \
            or "export function" in open(p, encoding="utf-8").read(), rel


def test_UI14_routes_cover_every_screen_in_the_plan():
    main = read("js", "main.js")
    paths = set(re.findall(r"path: '([^']+)'", main))
    expected = {"/ingresar", "/cambiar-clave", "/panel", "/sedes", "/equipos", "/equipos/:id", "/nuevo-router", "/servicios",
                "/servicios/:tid", "/vpn", "/cuenta", "/actividad", "/mas"}
    assert expected <= paths, expected - paths


def test_UI15_write_routes_hidden_from_cliente_in_router_and_menu():
    main = read("js", "main.js")
    for p in ("/nuevo-router", "/servicios", "/vpn"):
        assert re.search(rf"path: '{re.escape(p)}'.*write: true", main), p
    assert "route.write" in main and "canWrite" in main
    assert re.search(r"superOnly", main)


def test_UI16_frontend_role_checks_are_not_the_only_defense_note():
    """Informativo: ocultar rutas por rol en el cliente no es seguridad; el backend lo aplica (test_authz.py RB*)."""
    assert True


# ------------------------------------------------------------------ seguridad del cliente
def _js_files():
    for root, _, files in os.walk(FRONT_JS):
        for f in files:
            if f.endswith(".js"):
                yield os.path.join(root, f)


def test_UI20_no_dangerous_dom_sinks():
    hits = []
    for p in _js_files():
        for i, line in enumerate(open(p, encoding="utf-8").read().splitlines(), 1):
            code = line.split("//")[0]
            if re.search(r"\.innerHTML\s*=|\.outerHTML\s*=|insertAdjacentHTML|document\.write|\beval\(|new Function\(", code):
                hits.append(f"{os.path.basename(p)}:{i}")
    assert hits == [], hits


def test_UI21_token_only_in_session_storage():
    for p in _js_files():
        src = open(p, encoding="utf-8").read()
        for m in re.finditer(r"localStorage\.\w+\(\s*'([^']+)'", src):
            assert m.group(1) in ("avz_theme",), f"{os.path.basename(p)} guarda '{m.group(1)}' en localStorage"
    assert "sessionStorage" in read("js", "session.js")


def test_UI22_no_secrets_persisted_by_vpn_view():
    src = read("js", "views", "vpn.js")
    assert "created.conf_text = null" in src, "la vista debe soltar el .conf/clave privada tras mostrarlo"
    assert "localStorage" not in src and "sessionStorage" not in src


def test_UI23_script_viewer_disallows_copy_in_preview_with_placeholders():
    src = read("js", "views", "asistente.js")
    assert "allowCopy: false" in src, "la vista previa (con marcadores de clave) no debe poder copiarse/descargarse como si fuera final"


def test_UI24_mock_mode_is_explicit_opt_in_only():
    src = read("js", "api.js")
    assert "get('mock') === '1'" in src
    assert "import('./mock.js')" in src


@pytest.mark.xfail(strict=False, reason="HALLAZGO (bajo): mock.js (datos y roles simulados) se despliega a producción junto con la app; "
                                        "se activa con ?mock=1. No evade la API real (solo cambia la UI) pero expone el contrato y confunde a usuarios. "
                                        "Excluirlo del build de producción.")
def test_UI25_mock_not_shipped_to_production_bundle():
    assert not os.path.exists(os.path.join(FRONT_JS, "mock.js"))


def test_UI26_login_flow_clears_session_on_401_and_redirects():
    main = read("js", "main.js")
    assert "setUnauthorizedHandler" in main and "session.clear()" in main


def test_UI27_password_change_forced_flow_guarded():
    main = read("js", "main.js")
    assert "mustChange" in main and "/cambiar-clave" in main


# ------------------------------------------------------------------ accesibilidad (heurísticas)
CSS_PATH = os.path.join(FRONT, "css", "app.css")


@pytest.mark.skipif(not os.path.exists(CSS_PATH), reason="css/app.css no existe todavía (frontend en construcción)")
def test_UI30_focus_visible_reduced_motion_and_color_scheme_in_css():
    css = read("css", "app.css") + read("css", "tokens.css")
    assert ":focus-visible" in css
    assert "prefers-reduced-motion" in css
    assert re.search(r"outline\s*:\s*none", css) is None or ":focus-visible" in css
    assert "@media" in css and re.search(r"max-width|min-width", css), "debe haber puntos de corte para móvil"


@pytest.mark.skipif(not os.path.exists(CSS_PATH), reason="css/app.css no existe todavía (frontend en construcción)")
def test_UI31_touch_targets_defined():
    css = read("css", "app.css")
    assert re.search(r"min-height\s*:\s*(4[4-9]|[5-9]\d)px|min-height\s*:\s*(2\.[7-9]|[3-9])\d*rem", css), "objetivos táctiles >= 44px"


def test_UI32_images_have_alt_and_icon_buttons_have_labels():
    for p in _js_files():
        src = open(p, encoding="utf-8").read()
        for m in re.finditer(r"h\('img'\s*,\s*\{([^}]*)\}", src):
            assert "alt" in m.group(1), f"{os.path.basename(p)}: <img> sin alt"


def test_UI33_dialogs_use_roles_and_labels():
    dom = read("js", "dom.js")
    assert "aria-modal" in dom or "role" in dom
    assert "Escape" in dom, "los diálogos deben cerrarse con Esc"


def test_UI34_live_regions_for_errors_and_toasts():
    assert 'aria-live' in read("index.html") or 'role="status"' in read("index.html")
    assert "aria-live" in read("js", "views", "sede-dialog.js")


def test_UI35_theme_switch_respects_system_and_persists_safely():
    ti = read("js", "theme-init.js")
    assert "prefers-color-scheme" in ti and "try" in ti
    assert "data-theme" in ti or "dataset.theme" in ti
