/* ==========================================================================
   SIRIF · Sala de control · Valparaíso
   Cliente sin dependencias externas (Leaflet local + SVG inline)
   ========================================================================== */
(() => {
    'use strict';

    /* ----------------------------------------------------------------------
       Constantes
       ---------------------------------------------------------------------- */

    const ADVERTENCIA_RIESGO = 'Estimación de riesgo meteorológico; no es pronóstico de ocurrencia.';
    const ADVERTENCIA_VENTANA = 'Fuera de la ventana estacional de entrenamiento (jul–sep 2026)';

    const COORDENADAS = {
        quilpue: { lat: -33.0486, lng: -71.4425 },
        vina_del_mar: { lat: -33.0245, lng: -71.5518 },
        valparaiso: { lat: -33.0472, lng: -71.6127 }
    };

    const BASE_TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
    const BASE_TILE_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors';

    const COLORES_NIVEL = {
        ALTO: '#F43F5E',
        MEDIO: '#F59E0B',
        BAJO: '#10B981',
        SIN: '#64748B'
    };

    const NOMBRE_ROL = {
        admin: 'Administrador',
        analista: 'Analista',
        visualizador: 'Visualizador'
    };

    const TIPO_OBS = {
        vegetacion_seca: 'Vegetación seca',
        microbasural: 'Microbasural',
        quema: 'Quema',
        acceso_bloqueado: 'Acceso bloqueado',
        otro: 'Otro'
    };

    const TIPO_ACCION = {
        limpieza: 'Limpieza',
        fiscalizacion: 'Fiscalización',
        difusion: 'Difusión',
        otro: 'Otro'
    };

    const ORDEN_NIVEL = { ALTO: 0, MEDIO: 1, BAJO: 2, SIN: 3 };
    const ORDEN_VEGETACION = { alta: 3, media: 2, baja: 1 };

    const state = {
        token: '',
        rol: '',
        nombre: '',
        username: '',
        zonas: [],
        distritos: {},
        resumen: null,
        zonaActiva: localStorage.getItem('sirif_zona') || 'quilpue',
        vista: 'mapa',
        tab: 'diagnostico',
        opTab: 'observaciones',
        capas: { riesgo: true, infra: false, ndvi: false, obs: false },
        explicacion: null,
        variables: {},
        historial: [],
        recomendaciones: [],
        simResult: null,
        obs: [],
        acciones: [],
        bitacora: [],
        bitacoraZona: '',
        notificaciones: [],
        noLeidas: 0,
        notifFilter: 'todas',
        servicios: [],
        prioridad: null,
        rankingFormula: '',
        rankingPesos: {},
        rankingQuery: '',
        riskFilter: 'todos',
        sortKey: 'puntaje_prioridad',
        sortDir: 'desc',
        compare: [],
        comparacion: null,
        compareError: '',
        usuarios: [],
        usersError: '',
        userFormOpen: false,
        chatBusy: false,
        diagSeq: 0,
        map: null,
        geojson: null,
        districtsLayer: null,
        zoneLayer: null,
        overlays: { infra: null, ndvi: null, obs: null },
        mapHasFit: false,
        polling: []
    };

    /* ----------------------------------------------------------------------
       Utilidades
       ---------------------------------------------------------------------- */

    const $ = (selector, root = document) => root.querySelector(selector);
    const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

    /* ----------------------------------------------------------------------
       Tema claro / oscuro
       ---------------------------------------------------------------------- */

    const TEMA_KEY = 'sirif_tema';

    function temaActual() {
        return document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
    }

    function aplicarTema(tema, persistir = true) {
        const destino = tema === 'light' ? 'light' : 'dark';
        document.documentElement.dataset.theme = destino;
        const meta = document.querySelector('meta[name="color-scheme"]');
        if (meta) meta.setAttribute('content', destino === 'dark' ? 'dark light' : 'light dark');
        $$('[data-theme-toggle]').forEach((boton) => {
            const etiqueta = destino === 'dark' ? 'Cambiar a tema claro' : 'Cambiar a tema oscuro';
            boton.setAttribute('aria-label', etiqueta);
            boton.setAttribute('title', etiqueta);
            boton.setAttribute('aria-pressed', String(destino === 'light'));
        });
        if (persistir) {
            try { localStorage.setItem(TEMA_KEY, destino); } catch (error) { /* modo privado */ }
        }
        // El mapa pinta distritos y pines con colores resueltos en JS.
        if (state.map) {
            renderDistricts();
            renderZoneMarkers();
        }
    }

    function alternarTema() {
        aplicarTema(temaActual() === 'dark' ? 'light' : 'dark');
    }

    function esInvitado() {
        return document.body.classList.contains('is-guest');
    }


    const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
    }[char]));

    const icon = (name, className = 'icon') =>
        `<svg class="${className}" aria-hidden="true"><use href="#i-${name}"/></svg>`;

    const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

    const toNumber = (value) => {
        const num = Number(value);
        return Number.isFinite(num) ? num : null;
    };

    const num = (value, digits = 0) => {
        const n = toNumber(value);
        if (n === null) return '—';
        return new Intl.NumberFormat('es-CL', {
            minimumFractionDigits: digits,
            maximumFractionDigits: digits
        }).format(n);
    };

    const pct = (value, digits = 0) => {
        const n = toNumber(value);
        if (n === null) return '—';
        return `${num(n * 100, digits)} %`;
    };

    const pctPoints = (delta) => {
        const n = toNumber(delta);
        if (n === null) return '—';
        const points = Math.round(n * 100);
        if (points === 0) return '0 pts';
        return `${points > 0 ? '+' : '−'}${Math.abs(points)} pts`;
    };

    const levelClass = (level) => {
        const normalizado = String(level || 'SIN').toUpperCase();
        return Object.prototype.hasOwnProperty.call(COLORES_NIVEL, normalizado) ? normalizado : 'SIN';
    };

    const levelPill = (level) => {
        const clase = levelClass(level);
        return `<span class="level-pill level-${clase}">${clase === 'SIN' ? 'SIN DATOS' : clase}</span>`;
    };

    const zoneBySlug = (slug) => state.zonas.find((zona) => zona.zona === slug) || null;

    const zoneLabel = (slug) => {
        const zona = zoneBySlug(slug);
        if (zona) return zona.comuna || prettifySlug(slug);
        return prettifySlug(slug);
    };

    function prettifySlug(slug) {
        return String(slug || '')
            .replace(/_/g, ' ')
            .replace(/\b\w/g, (letra) => letra.toUpperCase());
    }

    function activeZone() {
        return zoneBySlug(state.zonaActiva);
    }

    function canWrite() {
        return state.rol === 'admin' || state.rol === 'analista';
    }

    function initials(value) {
        const partes = String(value || '').trim().split(/\s+/).filter(Boolean);
        if (!partes.length) return '–';
        return partes.slice(0, 2).map((parte) => parte[0]).join('').toUpperCase();
    }

    const dateFmt = new Intl.DateTimeFormat('es-CL', {
        day: '2-digit',
        month: 'short',
        hour: '2-digit',
        minute: '2-digit'
    });

    const shortDateFmt = new Intl.DateTimeFormat('es-CL', {
        day: '2-digit',
        month: '2-digit'
    });

    const timeFmt = new Intl.DateTimeFormat('es-CL', {
        hour: '2-digit',
        minute: '2-digit'
    });

    function formatDate(value) {
        if (!value) return 'sin fecha';
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return String(value);
        return dateFmt.format(date);
    }

    function relativeTime(value) {
        if (!value) return 'sin registro';
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return String(value);
        const minutes = (Date.now() - date.getTime()) / 60000;
        if (minutes < 1) return 'hace instantes';
        if (minutes < 60) return `hace ${Math.round(minutes)} min`;
        const hours = minutes / 60;
        if (hours < 24) return `hace ${hours.toFixed(1)} h`;
        return `hace ${(hours / 24).toFixed(1)} d`;
    }

    function relativeMinutes(minutes) {
        const value = toNumber(minutes);
        if (value === null) return 'sin registro';
        if (value < 1) return 'hace instantes';
        if (value < 60) return `hace ${Math.round(value)} min`;
        const hours = value / 60;
        if (hours < 24) return `hace ${hours.toFixed(1)} h`;
        return `hace ${(hours / 24).toFixed(1)} d`;
    }

    function toast(message, kind = 'info') {
        const contenedor = $('#toasts');
        if (!contenedor) return;
        const iconName = kind === 'ok' ? 'check' : kind === 'error' ? 'alert' : 'info';
        const nodo = document.createElement('div');
        nodo.className = `toast is-${kind}`;
        nodo.innerHTML = `${icon(iconName)}<span>${esc(message)}</span>`;
        contenedor.appendChild(nodo);
        window.setTimeout(() => {
            nodo.classList.add('is-leaving');
            window.setTimeout(() => nodo.remove(), 280);
        }, kind === 'error' ? 6000 : 3600);
    }

    /* ----------------------------------------------------------------------
       Transporte
       ---------------------------------------------------------------------- */

    async function api(path, options = {}) {
        const headers = new Headers(options.headers || {});
        if (state.token) headers.set('Authorization', `Bearer ${state.token}`);
        if (options.body && !headers.has('Content-Type')) {
            headers.set('Content-Type', 'application/json');
        }
        const response = await fetch(path, { ...options, headers });
        if (response.status === 401) {
            if (state.token) expireSession();
            throw new Error('Necesitas iniciar sesión para acceder a esta función.');
        }
        return response;
    }

    async function apiJson(path, options = {}) {
        const response = await api(path, options);
        if (!response.ok) {
            throw new Error(await errorMessage(response));
        }
        return response.json();
    }

    async function errorMessage(response) {
        try {
            const data = await response.json();
            if (typeof data.detail === 'string') return data.detail;
            if (Array.isArray(data.detail)) {
                return data.detail.map((item) => item.msg || JSON.stringify(item)).join('. ');
            }
        } catch (error) {
            /* respuesta sin JSON */
        }
        if (response.status === 403) return 'Tu perfil no tiene permiso para esta operación.';
        if (response.status === 429) return 'Demasiadas solicitudes. Espera unos segundos.';
        return `No fue posible completar la operación (${response.status}).`;
    }

    function expireSession() {
        ['sirif_token', 'sirif_rol', 'sirif_nombre', 'sirif_username'].forEach((key) => localStorage.removeItem(key));
        state.token = '';
        state.rol = '';
        state.nombre = '';
        state.username = '';
        window.location.reload();
    }

    function usernameFromToken(token) {
        try {
            const payload = String(token || '').split('.')[1] || '';
            const normalizado = payload.replace(/-/g, '+').replace(/_/g, '/');
            const conRelleno = normalizado + '='.repeat((4 - (normalizado.length % 4)) % 4);
            const decoded = JSON.parse(decodeURIComponent(escape(window.atob(conRelleno))));
            return decoded.sub || '';
        } catch (error) {
            return '';
        }
    }

    /* ----------------------------------------------------------------------
       Arranque y sesión
       ---------------------------------------------------------------------- */

    document.addEventListener('DOMContentLoaded', async () => {
        wireGlobalEvents();
        aplicarTema(temaActual(), false);
        await initMap();
        const token = localStorage.getItem('sirif_token');
        if (token) {
            state.token = token;
            state.rol = localStorage.getItem('sirif_rol') || '';
            state.nombre = localStorage.getItem('sirif_nombre') || '';
            state.username = localStorage.getItem('sirif_username') || usernameFromToken(token);
            $('#login').hidden = true;
            await enterApp();
        } else {
            await enterGuest();
        }
    });

    /** Acceso público: mapa de riesgo, sin JWT ni datos operativos. */
    async function enterGuest() {
        document.body.classList.add('is-guest');
        $('#login').hidden = true;
        applyGuestChrome();
        state.vista = 'mapa';
        await loadZonas();
        await loadDistritos();
        renderGuestPanel();
        if (state.map) {
            state.map.invalidateSize();
            state.mapHasFit = false;
            fitMapToData();
        }
        startPolling();
    }

    function applyGuestChrome() {
        $('#user-name').textContent = 'Invitado';
        $('#user-role').textContent = 'Acceso público';
        $('#user-initial').textContent = '–';
        $('#login-open-btn').hidden = false;
        $('#logout-btn').hidden = true;
        $('#notif-btn').hidden = true;
        $('.services-wrap').hidden = true;
        $('.quick-downloads').hidden = true;
        $('#refresh-btn').hidden = true;
        $$('.view-btn[data-view="tabla"]').forEach((boton) => {
            boton.classList.add('is-locked');
            boton.setAttribute('title', 'Inicia sesión para ver la tabla y el ranking');
        });
        $$('.layer-row[data-layer]:not([data-layer="riesgo"])').forEach((boton) => {
            boton.disabled = true;
            boton.setAttribute('aria-disabled', 'true');
            boton.setAttribute('title', 'Inicia sesión para usar esta capa');
        });
    }

    function applyAuthChrome() {
        $('#login-open-btn').hidden = true;
        $('#logout-btn').hidden = false;
        $('#notif-btn').hidden = false;
        $('.services-wrap').hidden = false;
        $('.quick-downloads').hidden = false;
        $('#refresh-btn').hidden = false;
        $$('.view-btn[data-view="tabla"]').forEach((boton) => {
            boton.classList.remove('is-locked');
            boton.removeAttribute('title');
        });
        $$('.layer-row[data-layer]:not([data-layer="riesgo"])').forEach((boton) => {
            boton.disabled = false;
            boton.removeAttribute('aria-disabled');
            boton.removeAttribute('title');
        });
    }

    function renderGuestPanel() {
        if (!esInvitado()) return;
        const contenedor = $('#diag-body');
        if (!contenedor) return;
        const zona = activeZone();
        const nivel = zona ? levelClass(zona.nivel_riesgo) : 'SIN';
        const titulo = zona ? (zona.comuna || prettifySlug(zona.zona)) : 'Región de Valparaíso';
        contenedor.innerHTML = `
            <section class="guest-panel">
                <p class="micro">Vista pública · riesgo meteorológico</p>
                <h3>Acceso público</h3>
                <p class="guest-reading">${esc(titulo)}${zona && zona.probabilidad != null ? ` · Probabilidad ${pct(zona.probabilidad)} · <span class="level-text level-${nivel}">${nivel}</span>` : ''}</p>
                <p class="guest-lede">El mapa de riesgo es abierto. Para operar la sala necesitas una cuenta asignada por el administrador.</p>
                <ul class="guest-features">
                    <li>${icon('activity')} Diagnóstico, factores e historial</li>
                    <li>${icon('layers')} Simulador de escenarios</li>
                    <li>${icon('alert')} Alertas y notificaciones</li>
                    <li>${icon('file')} Reportes PDF y exportación CSV</li>
                </ul>
                <button type="button" class="btn btn-primary guest-cta" data-open-login>
                    ${icon('user')}<span>Iniciar sesión</span>
                </button>
                <p class="guest-foot">¿Necesitas una cuenta? Solicítala al administrador del sistema.</p>
            </section>`;
        contenedor.querySelector('[data-open-login]')?.addEventListener('click', openLogin);
    }

    function openLogin() {
        const overlay = $('#login');
        overlay.hidden = false;
        overlay.classList.remove('is-leaving');
        $('#login-error').hidden = true;
        window.setTimeout(() => $('#login-user')?.focus(), 120);
    }

    function closeLogin() {
        const overlay = $('#login');
        overlay.classList.add('is-leaving');
        window.setTimeout(() => {
            overlay.hidden = true;
            overlay.classList.remove('is-leaving');
        }, 280);
        if (!state.token) {
            if (!esInvitado()) enterGuest();
            else window.setTimeout(() => state.map && state.map.invalidateSize(), 120);
        }
    }

    async function handleLogin(event) {
        event.preventDefault();
        const usuario = $('#login-user').value.trim();
        const password = $('#login-password').value;
        const error = $('#login-error');
        const boton = $('#login-submit');

        error.hidden = true;
        if (!usuario || !password) {
            error.textContent = 'Escribe tu usuario y contraseña.';
            error.hidden = false;
            return;
        }

        boton.disabled = true;
        boton.classList.add('is-busy');
        boton.querySelector('.btn-label').textContent = 'Verificando…';

        try {
            const cuerpo = new URLSearchParams({ username: usuario, password });
            const response = await fetch('/token', { method: 'POST', body: cuerpo });
            if (!response.ok) {
                throw new Error(response.status === 401
                    ? 'Usuario o contraseña incorrectos.'
                    : `No fue posible iniciar sesión (${response.status}).`);
            }
            const data = await response.json();
            state.token = data.access_token;
            state.rol = data.rol || '';
            state.nombre = data.nombre_completo || usuario;
            state.username = usuario;
            localStorage.setItem('sirif_token', state.token);
            localStorage.setItem('sirif_rol', state.rol);
            localStorage.setItem('sirif_nombre', state.nombre);
            localStorage.setItem('sirif_username', state.username);
            $('#login-password').value = '';
            $('#login').classList.add('is-leaving');
            window.setTimeout(() => { $('#login').hidden = true; }, 580);
            await enterApp();
            toast(`Sesión iniciada · ${NOMBRE_ROL[state.rol] || state.rol}`, 'ok');
        } catch (err) {
            error.textContent = err.message || 'No fue posible iniciar sesión.';
            error.hidden = false;
            $('#login-password').select();
        } finally {
            boton.disabled = false;
            boton.classList.remove('is-busy');
            boton.querySelector('.btn-label').textContent = 'Entrar a la sala';
        }
    }

    async function enterApp() {
        const eraInvitado = esInvitado();
        document.body.classList.remove('is-guest');
        $('#login').hidden = true;
        applyAuthChrome();
        applyUserChrome();
        applyRoleUI();
        if (eraInvitado && state.map) {
            window.setTimeout(() => {
                state.map.invalidateSize();
                state.mapHasFit = false;
                fitMapToData();
            }, 180);
        }
        await loadZonas();
        await Promise.allSettled([
            loadDistritos(),
            loadPanel(),
            loadServicios(),
            loadNotificaciones(),
            loadPrioridad(),
            loadObservaciones(),
            loadAcciones(),
            canWrite() ? loadBitacora() : Promise.resolve()
        ]);
        if (state.tab === 'usuarios' && state.rol === 'admin') await loadUsuarios();
        renderAllCore();
        await loadDiagnostico();
        startPolling();
    }

    function logout() {
        ['sirif_token', 'sirif_rol', 'sirif_nombre', 'sirif_username'].forEach((key) => localStorage.removeItem(key));
        window.location.reload();
    }

    function applyUserChrome() {
        $('#user-name').textContent = state.nombre || 'Sesión activa';
        $('#user-role').textContent = NOMBRE_ROL[state.rol] || state.rol || '—';
        $('#user-initial').textContent = initials(state.nombre);
    }

    function applyRoleUI() {
        const esAdmin = state.rol === 'admin';
        const tabUsuarios = $('#tab-usuarios');
        tabUsuarios.hidden = !esAdmin;
        if (!esAdmin && state.tab === 'usuarios') {
            setTab('diagnostico');
        }
        if (state.tab === 'operacion') renderOperacion();
    }

    /* ----------------------------------------------------------------------
       Carga principal
       ---------------------------------------------------------------------- */

    async function loadZonas() {
        try {
            const ruta = esInvitado() ? '/api/publico/zonas' : '/api/riesgo/todas';
            const data = await apiJson(ruta);
            state.zonas = Array.isArray(data.zonas) ? data.zonas : [];
            const slugs = state.zonas.map((zona) => zona.zona);
            if (!slugs.includes(state.zonaActiva)) {
                state.zonaActiva = slugs[0] || 'quilpue';
                localStorage.setItem('sirif_zona', state.zonaActiva);
            }
            renderZoneList();
            renderActiveHeader();
            renderTicker();
            renderZoneMarkers();
            fitMapToData();
            if (esInvitado()) renderGuestPanel();
        } catch (error) {
            state.zonas = [];
            $('#zone-list').innerHTML = `<div class="inline-error">${icon('alert')}<span>${esc(error.message)}</span></div>`;
            $('#active-zone-name').textContent = 'Sin datos';
            renderActiveHeader();
            renderTicker();
            renderZoneMarkers();
            if (esInvitado()) renderGuestPanel();
            toast(error.message, 'error');
        }
    }

    async function loadDistritos() {
        try {
            const ruta = esInvitado() ? '/api/publico/distritos' : '/api/datos/distritos';
            const data = await apiJson(ruta);
            state.distritos = (data && data.distritos) || {};
            renderDistricts();
            updateLayerCounts();
        } catch (error) {
            state.distritos = {};
            renderDistricts();
        }
    }

    async function loadPanel() {
        try {
            state.resumen = await apiJson('/api/panel/resumen');
        } catch (error) {
            state.resumen = null;
        }
        if (state.tab === 'diagnostico') renderDiagnostico();
    }

    async function loadServicios() {
        try {
            const data = await apiJson('/api/estado/servicios');
            state.servicios = Array.isArray(data.servicios) ? data.servicios : [];
            renderServicios();
        } catch (error) {
            state.servicios = [];
            $('#services-dot').className = 'signal-dot is-down';
            $('#services-label').textContent = 'Servicios sin datos';
            $('#service-list').innerHTML = `<div class="inline-error">${icon('alert')}<span>${esc(error.message)}</span></div>`;
        }
    }

    async function loadNotificaciones() {
        try {
            const data = await apiJson('/api/notificaciones');
            state.notificaciones = Array.isArray(data.notificaciones) ? data.notificaciones : [];
            state.noLeidas = Number(data.no_leidas || 0);
            renderNotifBadge();
            if ($('#notif-drawer').classList.contains('is-open')) renderNotificaciones();
        } catch (error) {
            if ($('#notif-drawer').classList.contains('is-open')) {
                $('#notif-list').innerHTML = `<div class="inline-error">${icon('alert')}<span>${esc(error.message)}</span></div>`;
            }
        }
    }

    async function loadPrioridad() {
        try {
            const data = await apiJson('/api/prioridad');
            state.prioridad = data;
            state.rankingFormula = data.formula || '';
            state.rankingPesos = data.pesos || {};
            renderFormula();
            if (state.vista === 'tabla') renderRanking();
        } catch (error) {
            state.prioridad = null;
            if (state.vista === 'tabla') {
                $('#table-wrap').innerHTML = `<div class="table-empty"><strong>No se pudo cargar la priorización</strong>${esc(error.message)}<button type="button" class="retry-btn" data-retry="prioridad">Reintentar</button></div>`;
            }
        }
    }

    async function loadObservaciones() {
        try {
            const data = await apiJson('/api/observaciones');
            state.obs = Array.isArray(data.observaciones) ? data.observaciones : [];
        } catch (error) {
            state.obs = [];
        }
        if (state.tab === 'operacion' && state.opTab === 'observaciones') renderOperacion();
    }

    async function loadAcciones() {
        try {
            const data = await apiJson('/api/acciones');
            state.acciones = Array.isArray(data.acciones) ? data.acciones : [];
        } catch (error) {
            state.acciones = [];
        }
        if (state.tab === 'operacion' && state.opTab === 'acciones') renderOperacion();
    }

    async function loadBitacora(zona = state.bitacoraZona) {
        if (!canWrite()) return;
        state.bitacoraZona = zona || '';
        try {
            const consulta = zona ? `?zona=${encodeURIComponent(zona)}` : '';
            const data = await apiJson(`/api/bitacora${consulta}`);
            state.bitacora = Array.isArray(data.entradas) ? data.entradas : [];
        } catch (error) {
            state.bitacora = [];
        }
        if (state.tab === 'operacion' && state.opTab === 'bitacora') renderOperacion();
    }

    function renderAllCore() {
        renderZoneList();
        renderActiveHeader();
        renderTicker();
        renderZoneMarkers();
        renderDistricts();
        updateLayerCounts();
        renderServicios();
        renderNotifBadge();
        if (state.tab === 'diagnostico') renderDiagnostico();
        if (state.tab === 'simulador') renderSimulador();
        if (state.tab === 'operacion') renderOperacion();
        if (state.tab === 'usuarios' && state.rol === 'admin') renderUsuarios();
        if (state.vista === 'tabla') renderRanking();
    }

    /* ----------------------------------------------------------------------
       Mapa
       ---------------------------------------------------------------------- */

    async function initMap() {
        if (state.map || typeof L === 'undefined') return;
        state.map = L.map('map', {
            zoomControl: false,
            attributionControl: false,
            minZoom: 8,
            maxZoom: 17,
            preferCanvas: true,
            zoomSnap: 0.25,
            wheelPxPerZoomLevel: 90
        });
        state.map.setView([-33.05, -71.5], 10);
        L.control.zoom({ position: 'bottomright' }).addTo(state.map);
        initBaseLayer();
        state.zoneLayer = L.layerGroup().addTo(state.map);

        try {
            const response = await fetch('/data/distritos_region.geojson');
            if (!response.ok) throw new Error('GeoJSON local no disponible');
            const geojson = await response.json();
            reprojectGeoJSON(geojson);
            state.geojson = geojson;
            renderDistricts();
        } catch (error) {
            $('#layer-status').textContent = 'Geometría local no disponible';
        }

        renderZoneMarkers();
        fitMapToData();
    }

    /**
     * Capa base cartográfica (CARTO Dark) sobre la grilla local.
     * La grilla de `#map` queda como respaldo: si no hay red, las teselas no
     * cargan, se retira la capa en silencio y el lienzo sigue legible.
     */
    function initBaseLayer() {
        if (!state.map) return;
        const lienzo = state.map.getContainer();
        if (navigator.onLine === false) {
            lienzo.classList.add('map--sin-base');
            return;
        }

        let teselasOk = 0;
        let teselasError = 0;
        let retirada = false;

        const capa = L.tileLayer(BASE_TILE_URL, {
            maxZoom: 19,
            crossOrigin: true,
            keepBuffer: 2,
            attribution: BASE_TILE_ATTRIBUTION,
            // Tesela transparente ante error: evita íconos roto y mantiene
            // visible la grilla de respaldo.
            errorTileUrl: 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'
        });

        capa.on('tileload', (evento) => {
            if (!evento.tile || evento.tile.naturalWidth <= 2) return;
            teselasOk += 1;
            if (teselasOk === 1) lienzo.classList.add('map--con-base');
        });

        capa.on('tileerror', () => {
            teselasError += 1;
            if (retirada || teselasOk > 0 || teselasError < 4) return;
            retirada = true;
            state.map.removeLayer(capa);
            state.baseLayer = null;
            lienzo.classList.remove('map--con-base');
            lienzo.classList.add('map--sin-base');
        });

        capa.addTo(state.map);
        state.baseLayer = capa;
    }

    function webMercatorToWGS84(x, y) {
        const lng = (x / 20037508.34) * 180;
        let lat = (y / 20037508.34) * 180;
        lat = (180 / Math.PI) * (2 * Math.atan(Math.exp((lat * Math.PI) / 180)) - Math.PI / 2);
        return [lng, lat];
    }

    function reprojectCoords(coords) {
        if (typeof coords[0] === 'number') {
            return webMercatorToWGS84(coords[0], coords[1]);
        }
        return coords.map(reprojectCoords);
    }

    function reprojectGeoJSON(geojson) {
        (geojson.features || []).forEach((feature) => {
            if (feature && feature.geometry && feature.geometry.coordinates) {
                feature.geometry.coordinates = reprojectCoords(feature.geometry.coordinates);
            }
        });
        return geojson;
    }

    function districtCode(feature) {
        const props = (feature && feature.properties) || {};
        return String(props.CODIGO_C17 || props.COD_DISTRI || '');
    }

    function zoneForDistrict(codigo) {
        return state.zonas.find((zona) => {
            const base = String(zona.codigo_comuna || '');
            return base && codigo.startsWith(base);
        }) || null;
    }

    function districtLevel(codigo) {
        const nivel = state.distritos[codigo];
        return nivel ? levelClass(nivel) : 'SIN';
    }

    function renderDistricts() {
        if (!state.map || !state.geojson) return;
        if (state.districtsLayer) {
            state.map.removeLayer(state.districtsLayer);
            state.districtsLayer = null;
        }
        const riesgoActivo = state.capas.riesgo;
        const temaClaro = temaActual() === 'light';

        state.districtsLayer = L.geoJSON(state.geojson, {
            style: (feature) => {
                const nivel = districtLevel(districtCode(feature));
                return {
                    color: riesgoActivo
                        ? (temaClaro ? 'rgba(14, 116, 144, 0.34)' : 'rgba(56, 189, 248, 0.36)')
                        : (temaClaro ? 'rgba(100, 116, 139, 0.22)' : 'rgba(148, 163, 184, 0.18)'),
                    weight: 1,
                    opacity: 0.95,
                    fillColor: COLORES_NIVEL[nivel] || COLORES_NIVEL.SIN,
                    fillOpacity: riesgoActivo
                        ? (nivel === 'SIN' ? 0.08 : (temaClaro ? 0.3 : 0.26))
                        : 0.035
                };
            },
            onEachFeature: (feature, layer) => {
                const codigo = districtCode(feature);
                layer.bindPopup(() => districtPopup(codigo));
                layer.on('mouseover', () => {
                    layer.setStyle({
                        weight: 1.6,
                        color: temaClaro ? 'rgba(14, 116, 144, 0.62)' : 'rgba(34, 211, 238, 0.72)',
                        fillOpacity: riesgoActivo ? 0.46 : 0.09
                    });
                });
                layer.on('mouseout', () => {
                    if (state.districtsLayer) state.districtsLayer.resetStyle(layer);
                });
                layer.on('click', () => {
                    const zona = zoneForDistrict(codigo);
                    if (zona) selectZone(zona.zona, { fly: false });
                });
            }
        }).addTo(state.map);

        state.districtsLayer.bringToBack();
        updateLayerCounts();
    }

    function districtPopup(codigo) {
        const zona = zoneForDistrict(codigo);
        const nivel = districtLevel(codigo);
        const factores = (zona && Array.isArray(zona.factores_predominantes))
            ? zona.factores_predominantes.slice(0, 3)
            : [];
        const factorHtml = factores.length
            ? `<div style="margin-top:7px;padding-top:7px;border-top:1px solid rgba(148,163,184,.14)">
                    ${factores.map((factor) => `
                        <div class="popup-factor">
                            <span>${esc(factor.etiqueta || factor.variable || 'Variable')}</span>
                            <b>${esc(factor.valor)} ${esc(factor.unidad || '')}</b>
                        </div>`).join('')}
               </div>`
            : '';
        const ventana = (zona && zona.fuera_ventana_estacional)
            ? `<p class="popup-note" style="color:#FCD34D">${esc(ADVERTENCIA_VENTANA)}</p>`
            : '';
        return `
            <div class="popup-title">
                <span>${esc(zona ? (zona.comuna || prettifySlug(zona.zona)) : `Distrito ${codigo}`)}</span>
                ${levelPill(nivel)}
            </div>
            <dl class="popup-grid">
                <dt>Distrito</dt><dd>${esc(codigo)}</dd>
                <dt>Riesgo</dt><dd>${nivel === 'SIN' ? 'Sin dato' : nivel}${zona ? ` · ${pct(zona.probabilidad)}` : ''}</dd>
                <dt>Lectura</dt><dd>Comunal</dd>
            </dl>
            <p class="popup-note">Modelo de riesgo; no es inferencia distrital ni pronóstico de ocurrencia.</p>
            ${factorHtml}
            ${ventana}
        `;
    }

    function mapaZonas() {
        if (state.zonas.length) return state.zonas;
        return Object.keys(COORDENADAS).map((slug) => ({
            zona: slug,
            comuna: prettifySlug(slug),
            nivel_riesgo: 'SIN',
            probabilidad: null
        }));
    }

    function renderZoneMarkers() {
        if (!state.map) return;
        if (state.zoneLayer) state.map.removeLayer(state.zoneLayer);
        state.zoneLayer = L.layerGroup().addTo(state.map);

        mapaZonas().forEach((zona) => {
            const coords = COORDENADAS[zona.zona];
            if (!coords) return;
            const nivel = levelClass(zona.nivel_riesgo);
            const activa = zona.zona === state.zonaActiva;
            const html = `
                <span class="pin">
                    <span class="pin-dot level-${nivel}"></span>
                    <span class="pin-name">${esc(zona.comuna || prettifySlug(zona.zona))}</span>
                    <span class="pin-pct">${pct(zona.probabilidad)}</span>
                </span>`;
            const marker = L.marker([coords.lat, coords.lng], {
                icon: L.divIcon({
                    className: `zone-pin${activa ? ' is-active' : ''}`,
                    html,
                    iconSize: [0, 0],
                    iconAnchor: [0, 0]
                }),
                keyboard: true,
                title: `${zona.comuna || prettifySlug(zona.zona)} · ${nivel}`,
                zIndexOffset: activa ? 1000 : 500
            });
            marker.on('click', () => selectZone(zona.zona, { fly: true }));
            marker.addTo(state.zoneLayer);
        });
    }

    function fitMapToData() {
        if (!state.map || state.mapHasFit) return;
        const bounds = L.latLngBounds([]);
        if (state.geojson && Array.isArray(state.geojson.features)) {
            state.geojson.features.forEach((feature) => {
                const featureBounds = L.geoJSON(feature).getBounds();
                if (!featureBounds.isValid()) return;
                const dentroRegion = featureBounds.getSouth() > -35
                    && featureBounds.getNorth() < -31.5
                    && featureBounds.getWest() > -73.5
                    && featureBounds.getEast() < -69.5;
                if (dentroRegion) bounds.extend(featureBounds);
            });
        }
        Object.values(COORDENADAS).forEach((coords) => bounds.extend([coords.lat, coords.lng]));
        if (!bounds.isValid()) return;
        // Un distrito del GeoJSON trae coordenadas fuera de la región; se excluye
        // del encuadre para no alejar el mapa de Valparaíso.
        // Se abre el marco ~18% por lado para mostrar la costa, el interior de
        // Quilpué/Limache y las comunas vecinas como contexto regional.
        bounds.pad(0.18);
        const invitado = document.body.classList.contains('is-guest');
        const compacto = window.innerWidth <= 900;
        state.map.fitBounds(bounds, {
            maxZoom: 11.5,
            animate: false,
            paddingTopLeft: invitado ? [40, 40] : compacto ? [54, 16] : [300, 20],
            paddingBottomRight: invitado ? [40, 40] : compacto ? [18, 54] : [22, 62]
        });
        state.mapHasFit = true;
        window.setTimeout(() => state.map.invalidateSize(), 120);
    }

    function selectZone(slug, options = {}) {
        if (!slug) return;
        if (esInvitado()) {
            state.zonaActiva = slug;
            localStorage.setItem('sirif_zona', slug);
            renderZoneList();
            renderActiveHeader();
            renderTicker();
            renderZoneMarkers();
            renderGuestPanel();
            if (options.fly !== false && state.map && COORDENADAS[slug]) {
                state.map.flyTo([COORDENADAS[slug].lat, COORDENADAS[slug].lng], Math.max(state.map.getZoom(), 11), {
                    duration: 0.7
                });
            }
            return;
        }
        state.zonaActiva = slug;
        localStorage.setItem('sirif_zona', slug);
        state.explicacion = null;
        state.variables = {};
        state.historial = [];
        state.recomendaciones = [];
        state.simResult = null;

        renderZoneList();
        renderActiveHeader();
        renderTicker();
        renderZoneMarkers();
        refreshOverlays();
        renderOperationZoneSelects();
        $('#diag-body').innerHTML = `
            <div class="skeleton skeleton-stats"></div>
            <div class="skeleton skeleton-block"></div>
            <div class="skeleton skeleton-block short"></div>`;
        loadDiagnostico();
        if (state.tab === 'simulador') {
            $('#sim-body').innerHTML = '<p class="empty-note">Cargando escenario base…</p>';
        }

        if (options.fly !== false && state.map && COORDENADAS[slug]) {
            state.map.flyTo([COORDENADAS[slug].lat, COORDENADAS[slug].lng], Math.max(state.map.getZoom(), 11), {
                duration: 0.7
            });
        }
    }

    /* ----------------------------------------------------------------------
       Selector de zona y cabecera
       ---------------------------------------------------------------------- */

    function renderZoneList() {
        const contenedor = $('#zone-list');
        if (!contenedor) return;
        if (!state.zonas.length) {
            contenedor.innerHTML = '<p class="empty-note">Sin zonas evaluadas.</p>';
            return;
        }
        const ordenadas = [...state.zonas].sort((a, b) =>
            (ORDEN_NIVEL[levelClass(a.nivel_riesgo)] - ORDEN_NIVEL[levelClass(b.nivel_riesgo)])
            || String(a.comuna || a.zona).localeCompare(String(b.comuna || b.zona), 'es')
        );
        contenedor.innerHTML = ordenadas.map((zona) => {
            const nivel = levelClass(zona.nivel_riesgo);
            const activa = zona.zona === state.zonaActiva;
            return `
                <button type="button" class="zone-row${activa ? ' is-active' : ''}"
                        role="option" aria-selected="${activa}" data-zone="${esc(zona.zona)}">
                    <span class="pin-dot level-${nivel}"></span>
                    <span class="zone-name">${esc(zona.comuna || prettifySlug(zona.zona))}</span>
                    <span class="zone-side">
                        <span class="zone-pct">${pct(zona.probabilidad)}</span>
                        <span class="zone-level level-${nivel}">${nivel === 'SIN' ? '—' : nivel}</span>
                    </span>
                </button>`;
        }).join('');
    }

    function lecturaLabel(zona) {
        // Distingue la escala del resultado: el modelo comunal (umbrales ~0.07)
        // y el regional (umbrales 0.20/0.08) no son comparables entre sí.
        return zona && zona.tipo_estimacion === 'riesgo_meteorologico_comunal'
            ? 'Lectura comunal'
            : 'Lectura regional';
    }

    // Umbrales OPERATIVOS de clasificación (deben coincidir con `risk-service`:
    // comunal 0.15/0.05 absolutos; regional 0.20/0.08).
    const UMBRALES_MODELO = {
        comunal: { medio: 0.05, alto: 0.15 },
        regional: { medio: 0.08, alto: 0.2 }
    };

    function umbralesZona(zona) {
        return zona && zona.tipo_estimacion === 'riesgo_meteorologico_comunal'
            ? UMBRALES_MODELO.comunal
            : UMBRALES_MODELO.regional;
    }

    function renderActiveHeader() {
        const zona = activeZone();
        if (!zona) {
            $('#active-zone-name').textContent = 'Sin zona';
            $('#active-level').className = 'level-pill level-SIN';
            $('#active-level').textContent = 'SIN DATOS';
            $('#active-meta').textContent = 'No hay evaluaciones disponibles';
            return;
        }
        const nivel = levelClass(zona.nivel_riesgo);
        $('#active-zone-name').textContent = zona.comuna || prettifySlug(zona.zona);
        $('#active-level').className = `level-pill level-${nivel}`;
        $('#active-level').textContent = nivel === 'SIN' ? 'SIN DATOS' : nivel;
        const actualizado = zona.antiguedad_minutos != null
            ? relativeMinutes(zona.antiguedad_minutos)
            : relativeTime(zona.fecha_evaluacion);
        $('#active-meta').textContent = `Probabilidad ${pct(zona.probabilidad)} · ${lecturaLabel(zona)} · ${actualizado} · ${zona.fuente || 'sin fuente'}`;
        renderOperationZoneSelects();
    }

    function renderTicker() {
        const zona = activeZone();
        const estacional = $('#ticker-season');
        const visible = !!(zona && zona.fuera_ventana_estacional);
        estacional.hidden = !visible;
        const meta = $('#ticker-meta');
        if (meta) {
            meta.textContent = zona
                ? `${zona.fuente || 'Open-Meteo'} · ${relativeMinutes(zona.antiguedad_minutos)}`
                : 'Sin lectura de datos';
        }
        const stamp = $('#map-stamp');
        if (stamp) {
            $('#stamp-text').textContent = zona
                ? `${zona.comuna || prettifySlug(zona.zona)} · ${relativeMinutes(zona.antiguedad_minutos)}`
                : 'Leyendo datos…';
        }
    }

    /* ----------------------------------------------------------------------
       Capas del mapa
       ---------------------------------------------------------------------- */

    function refreshOverlays() {
        state.capas.infra && loadInfraLayer();
        state.capas.ndvi && loadNdviLayer();
        state.capas.obs && loadObsLayer();
        updateLayerCounts();
    }

    function setLayer(name, enabled) {
        state.capas[name] = enabled;
        updateLayerButtons();
        if (name === 'riesgo') {
            renderDistricts();
            $('#layer-status').textContent = enabled
                ? `${Object.keys(state.distritos).length || 0} distritos con lectura de riesgo`
                : 'Capa de riesgo oculta';
            return;
        }
        if (!enabled) {
            if (state.overlays[name] && state.map) {
                state.map.removeLayer(state.overlays[name]);
            }
            state.overlays[name] = null;
            updateLayerCounts();
            return;
        }
        if (name === 'infra') loadInfraLayer();
        if (name === 'ndvi') loadNdviLayer();
        if (name === 'obs') loadObsLayer();
    }

    function updateLayerButtons() {
        $$('.layer-row[data-layer]').forEach((boton) => {
            const nombre = boton.dataset.layer;
            boton.setAttribute('aria-checked', String(!!state.capas[nombre]));
        });
    }

    function updateLayerCounts() {
        const distritos = Object.keys(state.distritos).length;
        const setText = (name, text) => {
            const nodo = document.querySelector(`[data-layer-count="${name}"]`);
            if (nodo) nodo.textContent = text;
            const boton = document.querySelector(`[data-layer="${name}"]`);
            if (boton) boton.setAttribute('aria-label', `${boton.textContent.trim().replace(/\s+/g, ' ')}: ${text}`);
        };
        setText('riesgo', `${distritos} distritos`);
        if (!state.capas.infra) setText('infra', 'Apagada');
        if (!state.capas.ndvi) setText('ndvi', 'Apagada');
        if (!state.capas.obs) setText('obs', 'Apagada');
        const activas = ['riesgo', 'infra', 'ndvi', 'obs'].filter((name) => state.capas[name]);
        $('#layer-status').textContent = `${distritos} distritos · capas: ${activas.map((name) => ({
            riesgo: 'riesgo',
            infra: 'infraestructura',
            ndvi: 'NDVI',
            obs: 'observaciones'
        }[name])).join(', ')}`;
    }

    function clearOverlay(name) {
        if (state.overlays[name] && state.map) {
            state.map.removeLayer(state.overlays[name]);
        }
        state.overlays[name] = null;
    }

    async function loadInfraLayer() {
        const zona = state.zonaActiva;
        clearOverlay('infra');
        const grupo = L.layerGroup().addTo(state.map);
        state.overlays.infra = grupo;
        try {
            const data = await apiJson(`/api/datos/${encodeURIComponent(zona)}/infraestructura`);
            if (state.overlays.infra !== grupo || !state.capas.infra || zona !== state.zonaActiva) {
                if (state.map) state.map.removeLayer(grupo);
                return;
            }
            const elementos = Array.isArray(data.elementos) ? data.elementos : [];
            let dibujados = 0;
            elementos.forEach((elemento) => {
                const lat = toNumber(elemento.latitud);
                const lng = toNumber(elemento.longitud);
                if (lat === null || lng === null) return;
                dibujados += 1;
                L.marker([lat, lng], {
                    icon: L.divIcon({ className: 'infra-pin', html: '<span class="marker"></span>', iconSize: [0, 0] }),
                    keyboard: false,
                    title: elemento.nombre || elemento.tipo || 'Infraestructura'
                }).bindPopup(`
                    <div class="popup-title"><span>${esc(elemento.nombre || elemento.tipo || 'Infraestructura')}</span><span class="tag state">${esc(elemento.tipo || 'infra')}</span></div>
                    <dl class="popup-grid">
                        <dt>Zona</dt><dd>${esc(zoneLabel(zona))}</dd>
                        <dt>Expuestos</dt><dd>${esc(elemento.expuestos != null ? num(elemento.expuestos) : 'sin dato')}</dd>
                        <dt>Dirección</dt><dd>${esc(elemento.direccion || 'sin dirección')}</dd>
                    </dl>
                    <p class="popup-note">Infraestructura crítica registrada en el sistema.</p>
                `).addTo(grupo);
            });
            setLayerCount('infra', dibujados ? `${dibujados} elementos` : 'sin elementos');
            if (!dibujados) toast('Sin infraestructura georreferenciada para la zona activa.', 'info');
        } catch (error) {
            setLayerCount('infra', 'error');
            toast(error.message, 'error');
        }
    }

    async function loadNdviLayer() {
        const zona = state.zonaActiva;
        const coords = COORDENADAS[zona];
        clearOverlay('ndvi');
        const grupo = L.layerGroup().addTo(state.map);
        state.overlays.ndvi = grupo;
        if (!coords) {
            setLayerCount('ndvi', 'sin coordenadas');
            return;
        }
        try {
            const data = await apiJson(`/api/datos/${encodeURIComponent(zona)}/vegetacion`);
            if (state.overlays.ndvi !== grupo || !state.capas.ndvi || zona !== state.zonaActiva) {
                if (state.map) state.map.removeLayer(grupo);
                return;
            }
            const capas = Array.isArray(data.capas) ? data.capas : [];
            if (!capas.length) {
                L.circleMarker([coords.lat, coords.lng], {
                    radius: 22,
                    color: 'rgba(148,163,184,.5)',
                    weight: 1,
                    dashArray: '3 4',
                    fillOpacity: 0.04
                }).bindPopup(`<div class="popup-title"><span>${esc(zoneLabel(zona))}</span><span class="tag">NDVI</span></div><p class="popup-note">Sin capas NDVI registradas para esta zona.</p>`).addTo(grupo);
                setLayerCount('ndvi', 'sin capas');
                return;
            }
            const colorCategoria = { seco: '#F43F5E', moderado: '#F59E0B', verde: '#10B981' };
            capas.forEach((capa, indice) => {
                const color = colorCategoria[String(capa.categoria || '').toLowerCase()] || '#64748B';
                L.circleMarker([coords.lat, coords.lng], {
                    radius: 16 + indice * 7,
                    color,
                    weight: 1.5,
                    fillColor: color,
                    fillOpacity: 0.08
                }).bindPopup(`
                    <div class="popup-title"><span>${esc(zoneLabel(zona))}</span><span class="tag" style="color:${color};border-color:${color}55">NDVI</span></div>
                    <dl class="popup-grid">
                        <dt>Índice</dt><dd>${esc(capa.indice || 'NDVI')}</dd>
                        <dt>Valor</dt><dd>${esc(capa.valor)}</dd>
                        <dt>Categoría</dt><dd>${esc(capa.categoria || 'sin categoría')}</dd>
                        <dt>Captura</dt><dd>${esc(capa.fecha_captura || 'sin fecha')}</dd>
                    </dl>
                    <p class="popup-note">Lectura de vegetación agregada por zona.</p>
                `).addTo(grupo);
            });
            setLayerCount('ndvi', `${capas.length} lecturas`);
        } catch (error) {
            setLayerCount('ndvi', 'sin datos');
            toast(error.message, 'error');
        }
    }

    async function loadObsLayer() {
        const zona = state.zonaActiva;
        clearOverlay('obs');
        const grupo = L.layerGroup().addTo(state.map);
        state.overlays.obs = grupo;
        try {
            const data = await apiJson(`/api/observaciones?zona=${encodeURIComponent(zona)}`);
            if (state.overlays.obs !== grupo || !state.capas.obs || zona !== state.zonaActiva) {
                if (state.map) state.map.removeLayer(grupo);
                return;
            }
            const observaciones = Array.isArray(data.observaciones) ? data.observaciones : [];
            let dibujadas = 0;
            observaciones.forEach((observacion) => {
                const lat = toNumber(observacion.latitud);
                const lng = toNumber(observacion.longitud);
                if (lat === null || lng === null) return;
                dibujadas += 1;
                const severidad = String(observacion.severidad || 'media');
                L.marker([lat, lng], {
                    icon: L.divIcon({
                        className: `obs-pin sev-${severidad}`,
                        html: '<span class="marker"></span>',
                        iconSize: [0, 0]
                    }),
                    keyboard: false,
                    title: TIPO_OBS[observacion.tipo] || observacion.tipo
                }).bindPopup(`
                    <div class="popup-title"><span>${esc(TIPO_OBS[observacion.tipo] || observacion.tipo)}</span><span class="tag sev-${severidad}">${esc(severidad)}</span></div>
                    <p style="margin:6px 0;color:#C3D2E2">${esc(observacion.descripcion)}</p>
                    <dl class="popup-grid">
                        <dt>Zona</dt><dd>${esc(zoneLabel(zona))}</dd>
                        <dt>Estado</dt><dd>${esc(observacion.estado || 'abierta')}</dd>
                        <dt>Registro</dt><dd>${esc(formatDate(observacion.created_at))}</dd>
                    </dl>
                `).addTo(grupo);
            });
            setLayerCount('obs', dibujadas ? `${dibujadas} registros` : 'sin coordenadas');
            if (!dibujadas) {
                toast(observaciones.length
                    ? 'Las observaciones de la zona no tienen coordenadas.'
                    : 'Sin observaciones registradas para la zona activa.', 'info');
            }
        } catch (error) {
            setLayerCount('obs', 'error');
            toast(error.message, 'error');
        }
    }

    function setLayerCount(name, text) {
        const nodo = document.querySelector(`[data-layer-count="${name}"]`);
        if (nodo) nodo.textContent = text;
        const boton = document.querySelector(`[data-layer="${name}"]`);
        if (boton) {
            const etiqueta = boton.querySelector('.layer-text')?.textContent?.trim().replace(/\s+/g, ' ') || name;
            boton.setAttribute('aria-label', `${etiqueta}: ${text}`);
        }
        const activas = ['infra', 'ndvi', 'obs'].filter((key) => state.capas[key]);
        if (activas.length) {
            $('#layer-status').textContent = activas
                .map((key) => `${({ infra: 'Infraestructura', ndvi: 'NDVI', obs: 'Observaciones' }[key])}: ${document.querySelector(`[data-layer-count="${key}"]`)?.textContent || ''}`)
                .join(' · ');
        }
    }

    /* ----------------------------------------------------------------------
       Diagnóstico
       ---------------------------------------------------------------------- */

    async function loadDiagnostico() {
        if (esInvitado()) {
            renderGuestPanel();
            return;
        }
        const zona = state.zonaActiva;
        const seq = ++state.diagSeq;
        if (!zona) return;

        const resultados = await Promise.allSettled([
            apiJson(`/api/riesgo/${encodeURIComponent(zona)}/explicacion`),
            apiJson(`/api/riesgo/${encodeURIComponent(zona)}/historial?limite=40`),
            apiJson(`/api/riesgo/${encodeURIComponent(zona)}/recomendaciones`)
        ]);

        if (seq !== state.diagSeq || zona !== state.zonaActiva) return;

        const [explicacion, historial, recomendaciones] = resultados;
        state.explicacion = explicacion.status === 'fulfilled' ? explicacion.value : null;
        state.variables = (state.explicacion && state.explicacion.variables) || {};
        state.historial = historial.status === 'fulfilled' && Array.isArray(historial.value.serie)
            ? historial.value.serie
            : [];
        state.recomendaciones = recomendaciones.status === 'fulfilled' && Array.isArray(recomendaciones.value.recomendaciones)
            ? recomendaciones.value.recomendaciones
            : [];

        renderDiagnostico();
        if (state.tab === 'simulador') renderSimulador();
    }

    function renderDiagnostico() {
        const contenedor = $('#diag-body');
        if (!contenedor) return;
        const zona = activeZone();
        if (!zona) {
            contenedor.innerHTML = `<div class="empty-state">${icon('map')}<strong>Sin zona seleccionada</strong>Selecciona una zona en el mapa para ver su diagnóstico.</div>`;
            return;
        }

        const niveles = state.resumen?.resumen_niveles || {
            ALTO: state.zonas.filter((item) => levelClass(item.nivel_riesgo) === 'ALTO').length,
            MEDIO: state.zonas.filter((item) => levelClass(item.nivel_riesgo) === 'MEDIO').length,
            BAJO: state.zonas.filter((item) => levelClass(item.nivel_riesgo) === 'BAJO').length
        };
        const explicacion = state.explicacion;
        const factores = (explicacion?.factores_predominantes || zona.factores_predominantes || [])
            .filter((factor) => factor && (factor.etiqueta || factor.variable))
            .slice(0, 5);
        const probabilidad = explicacion?.probabilidad ?? zona.probabilidad;
        const nivel = levelClass(explicacion?.nivel_riesgo || zona.nivel_riesgo);
        const needle = clamp((toNumber(probabilidad) || 0) * 100, 0, 100);
        const umbrales = umbralesZona(zona);
        const actualizado = zona.antiguedad_minutos != null
            ? relativeMinutes(zona.antiguedad_minutos)
            : relativeTime(zona.fecha_evaluacion);

        const factoresHtml = factores.length
            ? factores.map((factor) => {
                const peso = clamp((toNumber(factor.peso) || 0) * 100, 0, 100);
                const incrementa = String(factor.direccion || '').toLowerCase().includes('aument');
                const color = peso >= 85 ? 'is-coral' : peso >= 60 ? 'is-warm' : '';
                return `
                    <div class="factor-row">
                        <div class="factor-top">
                            <span class="factor-name">${esc(factor.etiqueta || factor.variable)}</span>
                            <span class="factor-reading">
                                <b>${esc(factor.valor ?? '—')}</b>
                                <span>${esc(factor.unidad || '')}</span>
                                <span class="factor-direction ${incrementa ? 'up' : 'down'}">${incrementa ? '↑ aumenta' : '↓ reduce'}</span>
                            </span>
                        </div>
                        <div class="factor-track"><span class="factor-fill ${color}" style="width:${peso}%"></span></div>
                    </div>`;
            }).join('')
            : '<p class="empty-note">Sin factores detallados para esta evaluación.</p>';

        const recomendacionesHtml = state.recomendaciones.length
            ? `<ol class="rec-list">${state.recomendaciones.map((rec) => `
                <li class="rec-item">
                    <span class="rec-number">${esc(rec.prioridad ?? '·')}</span>
                    <div class="rec-body">
                        <p>${esc(rec.texto)}</p>
                        <span class="rec-tag">${esc(rec.categoria || 'prevención')}</span>
                    </div>
                </li>`).join('')}</ol>`
            : '<p class="empty-note">Sin recomendaciones específicas para el nivel actual.</p>';

        contenedor.innerHTML = `
            <section class="block">
                <div class="block-head">
                    <span class="block-title">Resumen regional</span>
                    <span class="block-meta">${state.zonas.length} zonas</span>
                </div>
                <div class="summary-grid">
                    <div class="summary-card level-ALTO">
                        <span class="summary-label">Alto</span>
                        <span class="summary-value">${esc(niveles.ALTO ?? 0)}<span class="summary-unit">zonas</span></span>
                    </div>
                    <div class="summary-card level-MEDIO">
                        <span class="summary-label">Medio</span>
                        <span class="summary-value">${esc(niveles.MEDIO ?? 0)}<span class="summary-unit">zonas</span></span>
                    </div>
                    <div class="summary-card level-BAJO">
                        <span class="summary-label">Bajo</span>
                        <span class="summary-value">${esc(niveles.BAJO ?? 0)}<span class="summary-unit">zonas</span></span>
                    </div>
                </div>
            </section>

            <section class="block">
                <div class="block-head">
                    <span class="block-title">Probabilidad calibrada</span>
                    <span class="block-meta">${lecturaLabel(zona)} · ${esc(zona.version_modelo || 'modelo')}</span>
                </div>
                <div class="prob-hero">
                    <span class="prob-value ${nivel === 'ALTO' ? 'is-alto' : ''}">${probabilidad == null ? '—' : num(probabilidad * 100)}</span>
                    <span class="prob-unit">%</span>
                    <span class="prob-caption">
                        <strong>${nivel === 'SIN' ? 'Sin nivel' : nivel}</strong>
                        Lectura ${esc(actualizado)}
                    </span>
                </div>
                <div class="prob-rail" role="img" aria-label="Probabilidad calibrada ${pct(probabilidad)}; umbral medio ${pct(umbrales.medio)}, umbral alto ${pct(umbrales.alto)}">
                    <span class="prob-tick" style="left:${(umbrales.medio * 100).toFixed(1)}%"></span><span class="prob-tick-label" style="left:${(umbrales.medio * 100).toFixed(1)}%">${pct(umbrales.medio)}</span>
                    <span class="prob-tick" style="left:${(umbrales.alto * 100).toFixed(1)}%"></span><span class="prob-tick-label" style="left:${(umbrales.alto * 100).toFixed(1)}%">${pct(umbrales.alto)}</span>
                    <span class="prob-needle" style="--p:${needle}%;--needle:${COLORES_NIVEL[nivel] || COLORES_NIVEL.SIN}"></span>
                </div>
                <p class="explanation-line">${esc(explicacion?.explicacion || 'La explicación se construye con los factores predominantes de la última evaluación.')}</p>
            </section>

            <section class="block">
                <div class="block-head">
                    <span class="block-title">Factores predominantes</span>
                    <span class="block-meta">peso relativo</span>
                </div>
                <div class="factor-list">${factoresHtml}</div>
            </section>

            <section class="block">
                <div class="block-head">
                    <span class="block-title">Evolución temporal</span>
                    <span class="block-meta">${state.historial.length} evaluaciones</span>
                </div>
                ${buildHistoryChart(state.historial)}
            </section>

            <section class="block">
                <div class="block-head">
                    <span class="block-title">Recomendaciones preventivas</span>
                    <span class="block-meta">${state.recomendaciones.length} medidas</span>
                </div>
                ${recomendacionesHtml}
            </section>

            <section class="block">
                <p class="empty-note" style="padding:0;text-align:left;font-size:9.5px">${esc(ADVERTENCIA_RIESGO)}${zona.fuera_ventana_estacional ? ` ${esc(ADVERTENCIA_VENTANA)}.` : ''}</p>
            </section>
        `;
    }

    function buildHistoryChart(serie) {
        const puntos = [...(serie || [])]
            .map((item) => ({
                fecha: new Date(item.fecha_evaluacion),
                probabilidad: toNumber(item.probabilidad),
                nivel: levelClass(item.nivel_riesgo)
            }))
            .filter((item) => !Number.isNaN(item.fecha.getTime()) && item.probabilidad !== null)
            .sort((a, b) => a.fecha - b.fecha);

        if (!puntos.length) {
            return `<div class="history-shell"><div class="history-empty">Sin probabilidad registrada en el historial.</div></div>`;
        }

        const W = 380;
        const H = 138;
        const left = 30;
        const right = 8;
        const top = 10;
        const bottom = 22;
        const width = W - left - right;
        const height = H - top - bottom;
        const x = (indice) => puntos.length === 1
            ? left + width / 2
            : left + (indice / (puntos.length - 1)) * width;
        const y = (valor) => top + (1 - clamp(valor, 0, 1)) * height;

        const grid = [0, 0.5, 1].map((valor) => `
            <line x1="${left}" y1="${y(valor)}" x2="${W - right}" y2="${y(valor)}" stroke="rgba(148,163,184,.12)" stroke-width="1"/>
            <text x="${left - 6}" y="${y(valor) + 2.5}" text-anchor="end" fill="#5F7488" font-size="7" font-family="monospace">${Math.round(valor * 100)}%</text>
        `).join('');

        const line = puntos.map((punto, indice) => `${indice === 0 ? 'M' : 'L'}${x(indice).toFixed(1)},${y(punto.probabilidad).toFixed(1)}`).join(' ');
        const area = `${line} L${x(puntos.length - 1).toFixed(1)},${y(0)} L${x(0).toFixed(1)},${y(0)} Z`;
        const dots = puntos.map((punto, indice) => `
            <circle cx="${x(indice).toFixed(1)}" cy="${y(punto.probabilidad).toFixed(1)}" r="3.2"
                    fill="${COLORES_NIVEL[punto.nivel] || COLORES_NIVEL.SIN}" stroke="#0A1424" stroke-width="1.4">
                <title>${esc(formatDate(punto.fecha.toISOString()))} · ${pct(punto.probabilidad)} · ${punto.nivel}</title>
            </circle>
        `).join('');
        const first = shortDateFmt.format(puntos[0].fecha);
        const last = shortDateFmt.format(puntos[puntos.length - 1].fecha);
        const mismaFecha = puntos.every((punto) => punto.fecha.toDateString() === puntos[0].fecha.toDateString());
        const firstLabel = mismaFecha ? timeFmt.format(puntos[0].fecha) : first;
        const lastLabel = mismaFecha ? timeFmt.format(puntos[puntos.length - 1].fecha) : last;
        const thresholdY = y(0.5);

        return `
            <div class="history-shell">
                <svg class="history-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Evolución de la probabilidad calibrada en ${puntos.length} evaluaciones">
                    <defs>
                        <linearGradient id="historyArea" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="0%" stop-color="#06B6D4" stop-opacity="0.26"/>
                            <stop offset="100%" stop-color="#06B6D4" stop-opacity="0"/>
                        </linearGradient>
                    </defs>
                    ${grid}
                    <line x1="${left}" y1="${thresholdY}" x2="${W - right}" y2="${thresholdY}"
                          stroke="rgba(244,63,94,.5)" stroke-width="1" stroke-dasharray="3 4"/>
                    <text x="${W - right}" y="${thresholdY - 4}" text-anchor="end" fill="#FB7185" font-size="6.5" font-family="monospace">alerta 50 %</text>
                    <path d="${area}" fill="url(#historyArea)"/>
                    <path d="${line}" fill="none" stroke="#22D3EE" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>
                    ${dots}
                    <text x="${puntos.length === 1 ? x(0) : left}" y="${H - 6}" fill="#5F7488" font-size="7" font-family="monospace" text-anchor="${puntos.length === 1 ? 'middle' : 'start'}">${esc(firstLabel)}</text>
                    <text x="${W - right}" y="${H - 6}" text-anchor="end" fill="#5F7488" font-size="7" font-family="monospace">${esc(puntos.length === 1 ? '' : lastLabel)}</text>
                </svg>
                <div class="history-foot">
                    <span>probabilidad calibrada</span>
                    <span>${puntos.length === 1 ? 'primera lectura' : mismaFecha ? `${shortDateFmt.format(puntos[0].fecha)} · ${puntos.length} lecturas` : `${puntos.length} lecturas`}</span>
                </div>
            </div>`;
    }

    /* ----------------------------------------------------------------------
       Simulador
       ---------------------------------------------------------------------- */

    function renderSimulador() {
        const contenedor = $('#sim-body');
        if (!contenedor) return;
        const zona = activeZone();
        if (!zona) {
            contenedor.innerHTML = `<div class="empty-state">${icon('activity')}<strong>Sin escenario base</strong>Selecciona una zona con evaluación previa.</div>`;
            return;
        }

        const variables = state.variables || {};
        const base = {
            temperatura: clamp(toNumber(variables.temperatura) ?? 18, 0, 45),
            humedad: clamp(toNumber(variables.humedad) ?? 60, 0, 100),
            viento: clamp(toNumber(variables.viento) ?? 15, 0, 120),
            precipitacion: clamp(toNumber(variables.precipitacion) ?? 0, 0, 60)
        };

        const slider = (clave, etiqueta, unidad, minimo, maximo, paso, valor, icono) => `
            <div class="slider-field">
                <div class="slider-head">
                    <span class="slider-label">${icon(icono)}${etiqueta}</span>
                    <span class="slider-value"><output id="out-${clave}">${num(valor, 1)}</output><span>${unidad}</span></span>
                </div>
                <input type="range" id="sim-${clave}" name="${clave}" min="${minimo}" max="${maximo}" step="${paso}" value="${valor}"
                       aria-label="${etiqueta} en ${unidad}" style="--fill:${((valor - minimo) / (maximo - minimo)) * 100}%">
            </div>`;

        contenedor.innerHTML = `
            <p class="sim-lede">Ajusta las condiciones meteorológicas y compara el escenario con la última evaluación real de <strong>${esc(zona.comuna || prettifySlug(zona.zona))}</strong>.</p>
            <form id="sim-form" novalidate>
                <div class="sim-sliders">
                    ${slider('temperatura', 'Temperatura', '°C', 0, 45, 0.1, base.temperatura, 'temp')}
                    ${slider('humedad', 'Humedad relativa', '%', 0, 100, 1, base.humedad, 'drop')}
                    ${slider('viento', 'Viento', 'km/h', 0, 120, 0.5, base.viento, 'wind')}
                    ${slider('precipitacion', 'Precipitación', 'mm', 0, 60, 0.1, base.precipitacion, 'rain')}
                </div>
                <div class="sim-actions">
                    <button type="submit" class="btn btn-primary" id="sim-submit">${icon('activity')} Simular escenario</button>
                    <button type="button" class="btn btn-ghost" data-sim-reset>Restablecer</button>
                </div>
            </form>
            <div id="sim-result" class="sim-result">${state.simResult ? simResultHtml(state.simResult) : ''}</div>
            <div class="isolation-note is-cyan">
                ${icon('lock')}
                <span>Escenario aislado: la simulación no escribe en el historial real ni modifica la evaluación vigente.</span>
            </div>
        `;

        $$('#sim-body input[type="range"]').forEach(updateSliderFill);
    }

    function updateSliderFill(input) {
        const min = toNumber(input.min) ?? 0;
        const max = toNumber(input.max) ?? 100;
        const value = toNumber(input.value) ?? min;
        const porcentaje = max > min ? ((value - min) / (max - min)) * 100 : 0;
        input.style.setProperty('--fill', `${porcentaje}%`);
        const output = document.getElementById(`out-${input.name}`);
        if (output) output.textContent = num(value, 1);
    }

    async function runSimulation(form) {
        const zona = state.zonaActiva;
        const boton = $('#sim-submit');
        const resultado = $('#sim-result');
        const ajustes = {};
        ['temperatura', 'humedad', 'viento', 'precipitacion'].forEach((clave) => {
            const valor = toNumber(document.getElementById(`sim-${clave}`)?.value);
            if (valor !== null) ajustes[clave] = valor;
        });

        boton.disabled = true;
        boton.classList.add('is-busy');
        boton.innerHTML = `${icon('refresh')} Calculando…`;
        resultado.innerHTML = '<div class="skeleton skeleton-block"></div>';

        try {
            const data = await apiJson('/api/simular', {
                method: 'POST',
                body: JSON.stringify({ zona, ajustes })
            });
            if (zona !== state.zonaActiva) return;
            state.simResult = data;
            resultado.innerHTML = simResultHtml(data);
        } catch (error) {
            resultado.innerHTML = `<div class="inline-error">${icon('alert')}<span>${esc(error.message)}</span></div>`;
        } finally {
            boton.disabled = false;
            boton.classList.remove('is-busy');
            boton.innerHTML = `${icon('activity')} Simular escenario`;
        }
    }

    function simResultHtml(data) {
        if (!data) return '';
        const delta = toNumber(data.delta_probabilidad);
        const deltaClass = delta === null ? 'delta-flat' : delta > 0.0005 ? 'delta-up' : delta < -0.0005 ? 'delta-down' : 'delta-flat';
        const deltaLabel = delta === null
            ? 'Sin variación calculada'
            : delta > 0.0005
                ? 'Aumenta la probabilidad'
                : delta < -0.0005
                    ? 'Reduce la probabilidad'
                    : 'Sin variación';
        const factores = Array.isArray(data.factores_predominantes) ? data.factores_predominantes.slice(0, 3) : [];
        const factoresHtml = factores.length
            ? `<div class="factor-list" style="margin-top:10px">${factores.map((factor) => `
                <div class="factor-row">
                    <div class="factor-top">
                        <span class="factor-name">${esc(factor.etiqueta || factor.variable)}</span>
                        <span class="factor-reading"><b>${esc(factor.valor ?? '—')}</b><span>${esc(factor.unidad || '')}</span></span>
                    </div>
                    <div class="factor-track"><span class="factor-fill ${String(factor.direccion || '').includes('aument') ? 'is-coral' : ''}" style="width:${clamp((toNumber(factor.peso) || 0) * 100, 0, 100)}%"></span></div>
                </div>`).join('')}</div>`
            : '';

        return `
            <div class="block-head" style="margin-bottom:8px">
                <span class="block-title">Original vs. simulado</span>
                <span class="block-meta">modelo</span>
            </div>
            <div class="compare-grid">
                <div class="compare-card">
                    <span class="compare-label">Original</span>
                    <span class="compare-prob">${pct(data.original?.probabilidad)}</span>
                    <span class="compare-level">${esc(data.original?.nivel_riesgo || 'SIN DATOS')}</span>
                </div>
                <div class="compare-arrow">${icon('arrow')}</div>
                <div class="compare-card is-simulated">
                    <span class="compare-label">Simulado</span>
                    <span class="compare-prob" style="color:${COLORES_NIVEL[levelClass(data.simulado?.nivel_riesgo)]}">${pct(data.simulado?.probabilidad)}</span>
                    <span class="compare-level">${esc(data.simulado?.nivel_riesgo || 'SIN DATOS')}</span>
                </div>
            </div>
            <div class="delta-strip">
                <span>${deltaLabel}</span>
                <strong class="${deltaClass}">${delta === null ? '—' : pctPoints(delta)}</strong>
            </div>
            ${factoresHtml}
            <div class="isolation-note">
                ${icon('info')}
                <span>${esc(data.advertencia || 'Simulación sobre el modelo; no modifica el historial real.')}</span>
            </div>
            ${data.fuera_ventana_estacional ? `<div class="isolation-note">${icon('alert')}<span>${esc(ADVERTENCIA_VENTANA)}</span></div>` : ''}
        `;
    }

    /* ----------------------------------------------------------------------
       Operación territorial
       ---------------------------------------------------------------------- */

    function zoneOptions(selected) {
        const zonas = state.zonas.length
            ? state.zonas
            : Object.keys(COORDENADAS).map((slug) => ({ zona: slug, comuna: prettifySlug(slug) }));
        return zonas.map((zona) => `
            <option value="${esc(zona.zona)}" ${zona.zona === selected ? 'selected' : ''}>
                ${esc(zona.comuna || prettifySlug(zona.zona))}
            </option>`).join('');
    }

    function renderOperationZoneSelects() {
        $$('#op-body select[data-zone-select]').forEach((select) => {
            const actual = select.value || state.zonaActiva;
            select.innerHTML = zoneOptions(actual);
        });
    }

    function renderOperacion() {
        $$('.subtab').forEach((boton) => {
            const activo = boton.dataset.op === state.opTab;
            boton.classList.toggle('is-active', activo);
            boton.setAttribute('aria-selected', String(activo));
        });

        const contenedor = $('#op-body');
        if (!contenedor) return;
        if (state.opTab === 'observaciones') renderObservaciones(contenedor);
        else if (state.opTab === 'acciones') renderAcciones(contenedor);
        else renderBitacora(contenedor);
    }

    function renderObservaciones(contenedor) {
        const escritura = canWrite();
        const form = escritura ? `
            <form class="form-card" id="obs-form" novalidate>
                <div class="form-grid">
                    <label class="field field-compact full">
                        <span>Zona</span>
                        <select name="zona" data-zone-select>${zoneOptions(state.zonaActiva)}</select>
                    </label>
                    <label class="field field-compact">
                        <span>Tipo</span>
                        <select name="tipo">
                            ${Object.entries(TIPO_OBS).map(([valor, etiqueta]) => `<option value="${valor}">${esc(etiqueta)}</option>`).join('')}
                        </select>
                    </label>
                    <label class="field field-compact">
                        <span>Severidad</span>
                        <select name="severidad">
                            <option value="alta">Alta</option>
                            <option value="media" selected>Media</option>
                            <option value="baja">Baja</option>
                        </select>
                    </label>
                    <label class="field field-compact full">
                        <span>Descripción</span>
                        <textarea name="descripcion" placeholder="Qué observaste en terreno" required></textarea>
                    </label>
                    <label class="field field-compact">
                        <span>Latitud <em style="font-style:normal;color:var(--faint)">(opcional)</em></span>
                        <input name="latitud" type="number" step="0.0001" placeholder="-33.04">
                    </label>
                    <label class="field field-compact">
                        <span>Longitud <em style="font-style:normal;color:var(--faint)">(opcional)</em></span>
                        <input name="longitud" type="number" step="0.0001" placeholder="-71.44">
                    </label>
                </div>
                <div class="form-actions">
                    <button type="submit" class="btn btn-secondary">${icon('plus')} Registrar observación</button>
                </div>
            </form>` : `
            <div class="write-note">${icon('lock')}<span>Perfil de solo lectura: no puedes registrar observaciones ni cambiar su estado.</span></div>`;

        const items = state.obs.length
            ? state.obs.map((observacion) => `
                <article class="op-card sev-${esc(observacion.severidad || 'media')}">
                    <div class="op-card-top">
                        <span class="op-card-title">${esc(TIPO_OBS[observacion.tipo] || observacion.tipo)}</span>
                        <span class="tag sev-${esc(observacion.severidad || 'media')}">${esc(observacion.severidad || 'media')}</span>
                    </div>
                    <p class="op-card-desc">${esc(observacion.descripcion)}</p>
                    <div class="op-card-meta">
                        <span>${esc(zoneLabel(observacion.zona))}</span>
                        <span>${esc(observacion.estado || 'abierta')}</span>
                        <span>${esc(observacion.usuario || 'sistema')}</span>
                        <span>${esc(relativeTime(observacion.created_at))}</span>
                    </div>
                </article>`).join('')
            : `<div class="empty-state">${icon('map')}<strong>Sin observaciones</strong>No hay registros de terreno en el sistema.</div>`;

        contenedor.innerHTML = `
            <div class="op-section">
                <div class="block-head">
                    <span class="block-title">Observaciones en terreno</span>
                    <span class="block-meta">${state.obs.length} registros</span>
                </div>
                ${form}
            </div>
            <div class="op-section">
                <div class="op-list">${items}</div>
            </div>
        `;
    }

    function renderAcciones(contenedor) {
        const escritura = canWrite();
        const form = escritura ? `
            <form class="form-card" id="acc-form" novalidate>
                <div class="form-grid">
                    <label class="field field-compact full">
                        <span>Zona</span>
                        <select name="zona" data-zone-select>${zoneOptions(state.zonaActiva)}</select>
                    </label>
                    <label class="field field-compact full">
                        <span>Descripción</span>
                        <textarea name="descripcion" placeholder="Acción preventiva a ejecutar" required></textarea>
                    </label>
                    <label class="field field-compact">
                        <span>Tipo</span>
                        <select name="tipo">
                            ${Object.entries(TIPO_ACCION).map(([valor, etiqueta]) => `<option value="${valor}">${esc(etiqueta)}</option>`).join('')}
                        </select>
                    </label>
                    <label class="field field-compact">
                        <span>Prioridad</span>
                        <select name="prioridad">
                            <option value="alta">Alta</option>
                            <option value="media" selected>Media</option>
                            <option value="baja">Baja</option>
                        </select>
                    </label>
                    <label class="field field-compact full">
                        <span>Fecha programada</span>
                        <input name="fecha" type="date">
                    </label>
                </div>
                <div class="form-actions">
                    <button type="submit" class="btn btn-secondary">${icon('plus')} Crear acción</button>
                </div>
            </form>` : `
            <div class="write-note">${icon('lock')}<span>Perfil de solo lectura: no puedes crear ni actualizar acciones preventivas.</span></div>`;

        const items = state.acciones.length
            ? state.acciones.map((accion) => {
                const estado = String(accion.estado || 'pendiente');
                const siguiente = estado === 'pendiente' ? 'en_progreso' : 'completada';
                const etiquetaBoton = estado === 'pendiente' ? 'Iniciar' : 'Completar';
                const puedeAvanzar = escritura && estado !== 'completada' && estado !== 'cancelada';
                return `
                    <article class="op-card prio-${esc(accion.prioridad || 'media')}">
                        <div class="op-card-top">
                            <span class="op-card-title">${esc(accion.descripcion)}</span>
                            <span class="tag state">${esc(estado.replace(/_/g, ' '))}</span>
                        </div>
                        <div class="op-card-meta">
                            <span>${esc(zoneLabel(accion.zona))}</span>
                            <span>${esc(TIPO_ACCION[accion.tipo] || accion.tipo || 'acción')}</span>
                            <span class="tag prio-${esc(accion.prioridad || 'media')}">${esc(accion.prioridad || 'media')}</span>
                            <span>${esc(accion.responsable || 'sin responsable')}</span>
                            ${accion.fecha_programada ? `<span>${esc(formatDate(accion.fecha_programada))}</span>` : ''}
                        </div>
                        ${puedeAvanzar ? `<div class="op-card-actions"><button type="button" class="btn btn-secondary" data-accion-id="${esc(accion.id_accion)}" data-next-state="${siguiente}">${etiquetaBoton}</button></div>` : ''}
                    </article>`;
            }).join('')
            : `<div class="empty-state">${icon('check')}<strong>Sin acciones preventivas</strong>Crea la primera medida desde el formulario superior.</div>`;

        contenedor.innerHTML = `
            <div class="op-section">
                <div class="block-head">
                    <span class="block-title">Acciones preventivas</span>
                    <span class="block-meta">${state.acciones.length} registros</span>
                </div>
                ${form}
            </div>
            <div class="op-section"><div class="op-list">${items}</div></div>
        `;
    }

    function renderBitacora(contenedor) {
        if (!canWrite()) {
            contenedor.innerHTML = `<div class="empty-state">${icon('lock')}<strong>Bitácora restringida</strong>La bitácora de decisiones está disponible para perfiles admin y analista.</div>`;
            return;
        }
        const entradas = state.bitacora.length
            ? `<div class="timeline">${state.bitacora.map((entrada) => `
                <div class="timeline-item">
                    <div class="timeline-event">${esc(entrada.tipo_evento?.replace(/_/g, ' ') || 'evento')}${entrada.zona ? ` · ${esc(zoneLabel(entrada.zona))}` : ''}</div>
                    <div class="timeline-detail">${esc(entrada.detalle)}</div>
                    <div class="timeline-meta">${esc(entrada.usuario || 'sistema')} · ${esc(formatDate(entrada.created_at))}</div>
                </div>`).join('')}</div>`
            : `<div class="empty-state">${icon('clock')}<strong>Sin movimientos</strong>No hay entradas para el filtro seleccionado.</div>`;

        contenedor.innerHTML = `
            <div class="op-section">
                <div class="block-head">
                    <span class="block-title">Bitácora de decisiones</span>
                    <span class="block-meta">${state.bitacora.length} entradas</span>
                </div>
                <div class="form-grid" style="grid-template-columns:1fr auto;align-items:end">
                    <label class="field field-compact">
                        <span>Filtrar por zona</span>
                        <select id="bitacora-zona">
                            <option value="">Toda la región</option>
                            ${zoneOptions(state.bitacoraZona)}
                        </select>
                    </label>
                    <button type="button" class="btn btn-ghost" id="bitacora-refresh" style="margin-bottom:8px">${icon('refresh')} Actualizar</button>
                </div>
            </div>
            <div class="op-section">${entradas}</div>
        `;
    }

    async function crearObservacion(form) {
        const datos = new FormData(form);
        const cuerpo = {
            zona: String(datos.get('zona') || state.zonaActiva),
            tipo: String(datos.get('tipo') || 'otro'),
            descripcion: String(datos.get('descripcion') || '').trim(),
            severidad: String(datos.get('severidad') || 'media')
        };
        const latitud = toNumber(datos.get('latitud'));
        const longitud = toNumber(datos.get('longitud'));
        if (latitud !== null) cuerpo.latitud = latitud;
        if (longitud !== null) cuerpo.longitud = longitud;
        if (!cuerpo.descripcion) {
            toast('La descripción de la observación es obligatoria.', 'error');
            return;
        }
        const boton = form.querySelector('button[type="submit"]');
        boton.disabled = true;
        try {
            await apiJson('/api/observaciones', { method: 'POST', body: JSON.stringify(cuerpo) });
            toast('Observación registrada.', 'ok');
            await Promise.allSettled([loadObservaciones(), loadBitacora(), loadNotificaciones()]);
            if (state.capas.obs) loadObsLayer();
        } catch (error) {
            toast(error.message, 'error');
        } finally {
            boton.disabled = false;
        }
    }

    async function crearAccion(form) {
        const datos = new FormData(form);
        const cuerpo = {
            zona: String(datos.get('zona') || state.zonaActiva),
            descripcion: String(datos.get('descripcion') || '').trim(),
            tipo: String(datos.get('tipo') || 'otro'),
            prioridad: String(datos.get('prioridad') || 'media')
        };
        const fecha = String(datos.get('fecha') || '');
        if (fecha) cuerpo.fecha_programada = fecha;
        if (!cuerpo.descripcion) {
            toast('La descripción de la acción es obligatoria.', 'error');
            return;
        }
        const boton = form.querySelector('button[type="submit"]');
        boton.disabled = true;
        try {
            await apiJson('/api/acciones', { method: 'POST', body: JSON.stringify(cuerpo) });
            toast('Acción preventiva creada.', 'ok');
            await Promise.allSettled([loadAcciones(), loadBitacora(), loadNotificaciones()]);
        } catch (error) {
            toast(error.message, 'error');
        } finally {
            boton.disabled = false;
        }
    }

    async function actualizarAccion(id, estado, boton) {
        if (!id) return;
        boton.disabled = true;
        try {
            await apiJson(`/api/acciones/${id}`, {
                method: 'PATCH',
                body: JSON.stringify({ estado })
            });
            toast(estado === 'completada' ? 'Acción completada.' : 'Acción iniciada.', 'ok');
            await Promise.allSettled([loadAcciones(), loadBitacora(), loadNotificaciones()]);
        } catch (error) {
            toast(error.message, 'error');
            boton.disabled = false;
        }
    }

    /* ----------------------------------------------------------------------
       Asistente
       ---------------------------------------------------------------------- */

    function appendChat(role, html, meta = '') {
        const log = $('#chat-log');
        if (!log) return null;
        const nodo = document.createElement('div');
        nodo.className = `msg ${role === 'user' ? 'is-user' : 'is-assistant'}`;
        nodo.innerHTML = `${html}${meta}`;
        log.appendChild(nodo);
        log.scrollTop = log.scrollHeight;
        return nodo;
    }

    async function askAssistant(pregunta) {
        const texto = String(pregunta || '').trim();
        if (!texto || state.chatBusy) return;
        state.chatBusy = true;
        const input = $('#chat-input');
        const enviar = $('#chat-form button[type="submit"]');
        appendChat('user', `<p>${esc(texto)}</p>`);
        input.value = '';
        autoResizeChatInput();
        enviar.disabled = true;

        const typing = appendChat('assistant', '<span class="typing" aria-label="Consultando"><span></span><span></span><span></span></span>');

        try {
            const data = await apiJson('/api/asistente/preguntar', {
                method: 'POST',
                body: JSON.stringify({ pregunta: texto })
            });
            const meta = `
                <div class="msg-meta">
                    <span>fuente</span><code>${esc(data.fuente || 'local-determinista')}</code>
                    ${data.datos_utilizados && data.datos_utilizados.zona ? `<span>· ${esc(zoneLabel(data.datos_utilizados.zona))}</span>` : ''}
                </div>
                ${data.fuera_ventana_estacional ? `<div class="msg-warning">${esc(ADVERTENCIA_VENTANA)}</div>` : ''}
                <div class="msg-meta" style="border:0;margin-top:4px;padding-top:0">${esc(data.advertencia || ADVERTENCIA_RIESGO)}</div>`;
            typing.innerHTML = `<p>${esc(data.respuesta || 'Sin respuesta.')}</p>${meta}`;
            $('#chat-log').scrollTop = $('#chat-log').scrollHeight;
        } catch (error) {
            typing.innerHTML = `<div class="inline-error">${icon('alert')}<span>${esc(error.message)}</span></div>`;
        } finally {
            state.chatBusy = false;
            enviar.disabled = false;
            input.focus();
        }
    }

    function autoResizeChatInput() {
        const input = $('#chat-input');
        if (!input) return;
        input.style.height = '38px';
        input.style.height = `${Math.min(108, input.scrollHeight)}px`;
    }

    /* ----------------------------------------------------------------------
       Usuarios
       ---------------------------------------------------------------------- */

    async function loadUsuarios() {
        if (state.rol !== 'admin') return;
        try {
            const data = await apiJson('/api/usuarios');
            state.usuarios = Array.isArray(data.usuarios) ? data.usuarios : [];
            state.usersError = '';
        } catch (error) {
            state.usersError = error.message;
        }
        renderUsuarios();
    }

    function renderUsuarios() {
        const contenedor = $('#users-body');
        if (!contenedor) return;
        if (state.rol !== 'admin') {
            contenedor.innerHTML = `<div class="empty-state">${icon('lock')}<strong>Acceso restringido</strong>La gestión de usuarios requiere perfil administrador.</div>`;
            return;
        }
        if (state.usersError) {
            contenedor.innerHTML = `<div class="inline-error">${icon('alert')}<span>${esc(state.usersError)}</span></div><button type="button" class="retry-btn" data-retry="usuarios">Reintentar</button>`;
            return;
        }

        const formulario = state.userFormOpen ? `
            <form class="form-card" id="user-form" novalidate>
                <div class="form-grid">
                    <label class="field field-compact">
                        <span>Usuario</span>
                        <input name="username" autocomplete="off" spellcheck="false" required placeholder="nombre.usuario">
                    </label>
                    <label class="field field-compact">
                        <span>Nombre completo</span>
                        <input name="nombre_completo" autocomplete="off" placeholder="Nombre y apellido">
                    </label>
                    <label class="field field-compact">
                        <span>Rol</span>
                        <select name="rol">
                            <option value="analista">Analista</option>
                            <option value="visualizador">Visualizador</option>
                            <option value="admin">Administrador</option>
                        </select>
                    </label>
                    <label class="field field-compact">
                        <span>Contraseña</span>
                        <input name="password" type="password" autocomplete="new-password" minlength="8" required placeholder="Mínimo 8 caracteres">
                    </label>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-ghost" id="user-cancel">Cancelar</button>
                    <button type="submit" class="btn btn-secondary">${icon('plus')} Crear cuenta</button>
                </div>
            </form>` : '';

        const filas = state.usuarios.length
            ? state.usuarios.map((usuario) => {
                const esPropio = usuario.username === state.username;
                return `
                    <div class="user-row${usuario.activo ? '' : ' is-inactive'}">
                        <span class="avatar" aria-hidden="true">${esc(initials(usuario.nombre_completo || usuario.username))}</span>
                        <div class="user-row-body">
                            <div class="user-row-top">
                                <span class="user-row-name">
                                    ${esc(usuario.nombre_completo || usuario.username)}${esPropio ? '<span class="self-tag">tú</span>' : ''}
                                    <small>${esc(usuario.username)} · ${esc(formatDate(usuario.created_at))}</small>
                                </span>
                                <span class="user-row-controls">
                                    <select class="role-select" data-user-role="${esc(usuario.id_usuario)}" ${esPropio ? 'disabled' : ''} aria-label="Rol de ${esc(usuario.username)}">
                                        <option value="admin" ${usuario.rol === 'admin' ? 'selected' : ''}>Admin</option>
                                        <option value="analista" ${usuario.rol === 'analista' ? 'selected' : ''}>Analista</option>
                                        <option value="visualizador" ${usuario.rol === 'visualizador' ? 'selected' : ''}>Visualizador</option>
                                    </select>
                                    <button type="button" class="switch switch-btn ${usuario.activo ? 'is-on' : ''}"
                                            data-user-toggle="${esc(usuario.id_usuario)}" data-user-active="${usuario.activo}"
                                            aria-pressed="${usuario.activo}" aria-label="${usuario.activo ? 'Desactivar' : 'Activar'} a ${esc(usuario.username)}"
                                            ${esPropio ? 'disabled' : ''}></button>
                                </span>
                            </div>
                            <div class="user-row-meta">${usuario.activo ? 'cuenta activa' : 'cuenta desactivada'} · ${esc(NOMBRE_ROL[usuario.rol] || usuario.rol)}</div>
                        </div>
                    </div>`;
            }).join('')
            : `<div class="empty-state">${icon('user')}<strong>Sin usuarios</strong>No hay cuentas registradas.</div>`;

        contenedor.innerHTML = `
            <div class="users-toolbar">
                <span class="users-count">${state.usuarios.length} cuentas · ${state.usuarios.filter((usuario) => usuario.activo).length} activas</span>
                <button type="button" class="btn btn-secondary" id="user-new">${icon(state.userFormOpen ? 'close' : 'plus')} ${state.userFormOpen ? 'Cerrar' : 'Nuevo usuario'}</button>
            </div>
            ${formulario}
            <div class="user-list">${filas}</div>
            <p class="empty-note" style="text-align:left;padding:12px 2px 0;font-size:9px">Los cambios de rol y estado se aplican de inmediato. No puedes desactivar tu propia cuenta.</p>
        `;
    }

    async function crearUsuario(form) {
        const datos = new FormData(form);
        const cuerpo = {
            username: String(datos.get('username') || '').trim(),
            nombre_completo: String(datos.get('nombre_completo') || '').trim() || null,
            rol: String(datos.get('rol') || 'visualizador'),
            password: String(datos.get('password') || '')
        };
        if (!cuerpo.username || cuerpo.password.length < 8) {
            toast('Usuario obligatorio y contraseña de al menos 8 caracteres.', 'error');
            return;
        }
        const boton = form.querySelector('button[type="submit"]');
        boton.disabled = true;
        try {
            await apiJson('/api/usuarios', { method: 'POST', body: JSON.stringify(cuerpo) });
            toast('Cuenta creada.', 'ok');
            state.userFormOpen = false;
            await loadUsuarios();
        } catch (error) {
            toast(error.message, 'error');
        } finally {
            boton.disabled = false;
        }
    }

    async function actualizarUsuario(id, cambios, control) {
        if (!id) return;
        if (control) control.disabled = true;
        try {
            await apiJson(`/api/usuarios/${id}`, {
                method: 'PATCH',
                body: JSON.stringify(cambios)
            });
            toast('Usuario actualizado.', 'ok');
            await loadUsuarios();
        } catch (error) {
            toast(error.message, 'error');
            if (control) control.disabled = false;
        }
    }

    /* ----------------------------------------------------------------------
       Ranking y comparador
       ---------------------------------------------------------------------- */

    function setVista(vista) {
        if (vista !== 'mapa' && vista !== 'tabla') return;
        if (esInvitado() && vista === 'tabla') {
            toast('Inicia sesión para ver la tabla y el ranking territorial.', 'info');
            openLogin();
            return;
        }
        state.vista = vista;
        $$('.view-btn').forEach((boton) => {
            const activo = boton.dataset.view === vista;
            boton.classList.toggle('is-active', activo);
            boton.setAttribute('aria-pressed', String(activo));
        });
        $('#view-mapa').hidden = vista !== 'mapa';
        $('#view-tabla').hidden = vista !== 'tabla';
        if (vista === 'tabla') {
            if (state.prioridad) renderRanking();
            else if (!state.prioridad) loadPrioridad();
            renderFormula();
        } else if (state.map) {
            window.setTimeout(() => state.map.invalidateSize(), 80);
        }
    }

    function sortValue(zona, key) {
        const factores = zona.factores || {};
        const valores = {
            posicion: zona.posicion,
            comuna: zona.comuna || zona.zona,
            nivel_riesgo: ORDEN_NIVEL[levelClass(zona.nivel_riesgo)],
            probabilidad: toNumber(zona.probabilidad),
            puntaje_prioridad: toNumber(zona.puntaje_prioridad),
            viviendas: toNumber(factores.viviendas_expuestas),
            infraestructura: toNumber(factores.infraestructura_total),
            vegetacion: ORDEN_VEGETACION[String(factores.vegetacion || '').toLowerCase()] || 0,
            poblacion: toNumber(factores.poblacion)
        };
        return valores[key] ?? 0;
    }

    function renderRanking() {
        const contenedor = $('#table-wrap');
        if (!contenedor) return;
        if (!state.prioridad) {
            contenedor.innerHTML = `<div class="table-loading"><div class="skeleton skeleton-row"></div><div class="skeleton skeleton-row"></div><div class="skeleton skeleton-row"></div></div>`;
            return;
        }
        const zonas = Array.isArray(state.prioridad.zonas) ? [...state.prioridad.zonas] : [];
        const consulta = state.rankingQuery.trim().toLowerCase();
        const filtradas = zonas
            .filter((zona) => {
                const texto = `${zona.comuna || ''} ${zona.zona || ''}`.toLowerCase();
                const nivel = levelClass(zona.nivel_riesgo);
                return (!consulta || texto.includes(consulta))
                    && (state.riskFilter === 'todos' || nivel === state.riskFilter);
            })
            .sort((a, b) => {
                const va = sortValue(a, state.sortKey);
                const vb = sortValue(b, state.sortKey);
                let resultado;
                if (typeof va === 'string' || typeof vb === 'string') {
                    resultado = String(va).localeCompare(String(vb), 'es');
                } else {
                    resultado = va - vb;
                }
                return state.sortDir === 'asc' ? resultado : -resultado;
            });

        const columnas = [
            { key: 'posicion', label: '#', className: 'col-rank' },
            { key: 'comuna', label: 'Zona' },
            { key: 'nivel_riesgo', label: 'Nivel' },
            { key: 'probabilidad', label: 'Probabilidad' },
            { key: 'puntaje_prioridad', label: 'Prioridad' },
            { key: 'viviendas', label: 'Viviendas' },
            { key: 'infraestructura', label: 'Infraestructura' },
            { key: 'vegetacion', label: 'Vegetación' },
            { key: 'poblacion', label: 'Población' }
        ];

        const thead = columnas.map((columna) => {
            const ordenada = state.sortKey === columna.key;
            const direccion = ordenada ? (state.sortDir === 'asc' ? 'ascending' : 'descending') : 'none';
            const glifo = ordenada ? (state.sortDir === 'asc' ? '▲' : '▼') : '↕';
            return `<th scope="col" class="${columna.className || ''}${ordenada ? ' is-sorted' : ''}" aria-sort="${direccion}">
                <button type="button" data-sort="${columna.key}">${columna.label}<span class="sort-glyph" aria-hidden="true">${glifo}</span></button>
            </th>`;
        }).join('');

        const filas = filtradas.map((zona) => {
            const nivel = levelClass(zona.nivel_riesgo);
            const factores = zona.factores || {};
            const prioridad = state.prioridad.zonas.find((item) => item.zona === zona.zona)
                || state.prioridad.zonas.find((item) => item.comuna === zona.comuna)
                || zona;
            const marcada = state.compare.includes(zona.zona);
            const deshabilitada = !marcada && state.compare.length >= 3;
            return `
                <tr class="${marcada ? 'is-selected' : ''}">
                    <td class="cell-check"><input type="checkbox" data-compare="${esc(zona.zona)}" ${marcada ? 'checked' : ''} ${deshabilitada ? 'disabled' : ''} aria-label="Comparar ${esc(zona.comuna || zona.zona)}"></td>
                    <td><span class="rank-num">${String(zona.posicion ?? prioridad.posicion ?? '—').padStart(2, '0')}</span></td>
                    <td>
                        <span class="zone-cell">
                            <span class="pin-dot level-${nivel}"></span>
                            <span><strong>${esc(zona.comuna || prettifySlug(zona.zona))}</strong><small>${esc(zona.zona)}</small></span>
                        </span>
                    </td>
                    <td>${levelPill(nivel)}</td>
                    <td class="prob-cell">${pct(zona.probabilidad)}</td>
                    <td>
                        <span class="score-cell">
                            <span class="score-bar"><span style="width:${clamp(toNumber(zona.puntaje_prioridad) || 0, 0, 100)}%"></span></span>
                            <b>${esc(zona.puntaje_prioridad ?? '—')}</b>
                        </span>
                    </td>
                    <td class="num-cell">${num(factores.viviendas_expuestas)}</td>
                    <td class="num-cell">${num(factores.infraestructura_total)}</td>
                    <td class="veg-cell">${esc(factores.vegetacion || '—')}</td>
                    <td class="num-cell">${num(factores.poblacion)}</td>
                    <td><button type="button" class="row-action" data-open-zone="${esc(zona.zona)}">Abrir</button></td>
                </tr>`;
        }).join('');

        contenedor.innerHTML = filtradas.length
            ? `<table class="rank-table">
                    <thead><tr><th class="col-check" scope="col"><span class="sr-only">Comparar</span></th>${thead}<th scope="col"><span class="sr-only">Acciones</span></th></tr></thead>
                    <tbody>${filas}</tbody>
               </table>`
            : `<div class="table-empty"><strong>Sin zonas para el filtro</strong>Ajusta la búsqueda o el nivel de riesgo.</div>`;
    }

    function renderFormula() {
        const formula = $('#ranking-formula');
        const pesos = $('#ranking-weights');
        if (formula) formula.textContent = state.rankingFormula || 'La fórmula se carga con la priorización.';
        if (pesos) {
            pesos.innerHTML = Object.entries(state.rankingPesos || {}).map(([clave, valor]) => `
                <span class="weight-chip">${esc(clave)} <b>${Math.round(Number(valor) * 100)} %</b></span>`).join('');
        }
    }

    async function toggleCompare(zona, marcada) {
        if (marcada) {
            if (state.compare.length >= 3 || state.compare.includes(zona)) return;
            state.compare = [...state.compare, zona];
        } else {
            state.compare = state.compare.filter((item) => item !== zona);
        }
        renderRanking();
        await loadComparison();
    }

    async function loadComparison() {
        const cuerpo = $('#compare-body');
        if (!cuerpo) return;
        if (state.compare.length < 2) {
            state.comparacion = null;
            state.compareError = '';
            renderComparison();
            return;
        }
        const seleccion = [...state.compare].join(',');
        cuerpo.innerHTML = `<div class="skeleton skeleton-block short"></div>`;
        try {
            const data = await apiJson(`/api/comparar?zonas=${encodeURIComponent(seleccion)}`);
            if (seleccion !== state.compare.join(',')) return;
            state.comparacion = data;
            state.compareError = '';
        } catch (error) {
            state.comparacion = null;
            state.compareError = error.message;
        }
        renderComparison();
    }

    function renderComparison() {
        const cuerpo = $('#compare-body');
        if (!cuerpo) return;
        if (state.compareError) {
            cuerpo.innerHTML = `<div class="inline-error">${icon('alert')}<span>${esc(state.compareError)}</span></div><button type="button" class="retry-btn" data-retry="comparar">Reintentar</button>`;
            return;
        }
        if (state.compare.length < 2 || !state.comparacion) {
            cuerpo.innerHTML = `
                <div class="compare-empty">
                    ${icon('table')}
                    <p>${state.compare.length === 1
                        ? `Has elegido <strong>${esc(zoneLabel(state.compare[0]))}</strong>. Marca al menos otra zona para comparar.`
                        : 'Marca hasta tres zonas en la tabla para contrastar su última evaluación real.'}</p>
                </div>`;
            return;
        }

        const prioridadPorZona = {};
        (state.prioridad?.zonas || []).forEach((zona) => {
            prioridadPorZona[zona.zona] = zona;
        });
        const maxima = Math.max(...state.comparacion.comparacion.map((zona) => toNumber(zona.probabilidad) || 0));
        const tarjetas = state.comparacion.comparacion.map((zona) => {
            const nivel = levelClass(zona.nivel_riesgo);
            const variables = zona.variables || {};
            const prioridad = prioridadPorZona[zona.zona] || {};
            const factores = prioridad.factores || {};
            const esMaxima = (toNumber(zona.probabilidad) || 0) === maxima;
            return `
                <article class="compare-zone${esMaxima ? ' is-best' : ''}">
                    <div class="compare-zone-top">
                        <span class="compare-zone-name">${esc(zona.comuna || prettifySlug(zona.zona))}</span>
                        ${levelPill(nivel)}
                    </div>
                    <div class="compare-prob-line">
                        <strong style="color:${COLORES_NIVEL[nivel]}">${pct(zona.probabilidad)}</strong>
                        <span>probabilidad</span>
                    </div>
                    <div class="compare-facts">
                        <span>Temp. <b>${esc(variables.temperatura ?? '—')} °C</b></span>
                        <span>Viento <b>${esc(variables.viento ?? '—')} km/h</b></span>
                        <span>Vegetación <b>${esc(variables.vegetacion || '—')}</b></span>
                        <span>Viviendas <b>${num(factores.viviendas_expuestas)}</b></span>
                    </div>
                    <div class="compare-note">${esMaxima ? 'Mayor probabilidad del grupo · ' : ''}${esc(formatDate(zona.ultima_evaluacion))}</div>
                </article>`;
        }).join('');

        cuerpo.innerHTML = `${tarjetas}<p class="compare-note" style="text-align:left;padding:6px 2px 0">Comparación sobre la última evaluación real de cada zona. ${esc(ADVERTENCIA_RIESGO)}</p>`;
    }

    /* ----------------------------------------------------------------------
       Notificaciones y servicios
       ---------------------------------------------------------------------- */

    function renderNotifBadge() {
        const badge = $('#notif-count');
        const total = Number(state.noLeidas || 0);
        badge.textContent = total > 99 ? '99+' : String(total);
        badge.hidden = total <= 0;
        $('#notif-btn').setAttribute('aria-label', `Abrir notificaciones · ${total} sin leer`);
        const contador = $('#notif-unread');
        if (contador) contador.textContent = String(total);
    }

    function renderNotificaciones() {
        const lista = $('#notif-list');
        if (!lista) return;
        renderNotifBadge();
        const filtradas = state.notifFilter === 'no-leidas'
            ? state.notificaciones.filter((notificacion) => !notificacion.leida)
            : state.notificaciones;
        if (!filtradas.length) {
            lista.innerHTML = `<p class="empty-note">${state.notifFilter === 'no-leidas' ? 'No hay notificaciones sin leer.' : 'Sin notificaciones registradas.'}</p>`;
            return;
        }
        lista.innerHTML = filtradas.map((notificacion) => `
            <button type="button" class="notif-item${notificacion.leida ? '' : ' is-unread'}" data-notif-id="${esc(notificacion.id_notificacion)}" data-notif-read="${notificacion.leida}">
                <span class="notif-topline">
                    <span class="notif-title">${esc(notificacion.titulo || 'Notificación')}</span>
                    ${notificacion.leida ? '' : '<span class="notif-unread-dot" aria-hidden="true"></span>'}
                </span>
                <span class="notif-message">${esc(notificacion.mensaje || '')}</span>
                <span class="notif-meta">
                    <span>${esc(String(notificacion.tipo || 'evento').replace(/_/g, ' '))}</span>
                    ${notificacion.zona ? `<span>· ${esc(zoneLabel(notificacion.zona))}</span>` : ''}
                    <span>· ${esc(relativeTime(notificacion.created_at))}</span>
                </span>
            </button>`).join('');
    }

    function setDrawer(open) {
        const drawer = $('#notif-drawer');
        const scrim = $('#drawer-scrim');
        const boton = $('#notif-btn');
        if (!drawer || !scrim) return;
        drawer.classList.toggle('is-open', open);
        drawer.setAttribute('aria-hidden', String(!open));
        boton.setAttribute('aria-expanded', String(open));
        if (open) {
            scrim.hidden = false;
            renderNotificaciones();
            window.setTimeout(() => $('#notif-close').focus(), 120);
        } else {
            scrim.hidden = true;
            boton.focus();
        }
    }

    async function marcarNotificacion(id) {
        const notificacion = state.notificaciones.find((item) => String(item.id_notificacion) === String(id));
        if (!notificacion || notificacion.leida) return;
        notificacion.leida = true;
        state.noLeidas = Math.max(0, state.noLeidas - 1);
        renderNotificaciones();
        try {
            await apiJson(`/api/notificaciones/${id}/leida`, { method: 'POST' });
        } catch (error) {
            notificacion.leida = false;
            state.noLeidas += 1;
            renderNotificaciones();
            toast(error.message, 'error');
        }
    }

    function renderServicios() {
        const lista = $('#service-list');
        if (!lista) return;
        if (!state.servicios.length) {
            $('#services-dot').className = 'signal-dot is-down';
            $('#services-label').textContent = 'Servicios sin datos';
            lista.innerHTML = '<p class="empty-note">Sin heartbeat registrado.</p>';
            return;
        }
        const totales = state.servicios.length;
        const ok = state.servicios.filter((servicio) => servicio.estado === 'ok').length;
        const degradados = state.servicios.filter((servicio) => servicio.estado === 'degradado').length;
        const caidos = state.servicios.filter((servicio) => servicio.estado === 'caido').length;
        const dot = $('#services-dot');
        dot.className = `signal-dot${caidos ? ' is-down' : degradados ? ' is-warn' : ''}`;
        $('#services-label').textContent = caidos || degradados
            ? `Servicios ${ok}/${totales}`
            : `Servicios ${ok}/${totales}`;
        const estados = { ok: 'Operativo', degradado: 'Degradado', caido: 'Caído' };
        lista.innerHTML = state.servicios.map((servicio) => {
            const estado = ['ok', 'degradado', 'caido'].includes(servicio.estado) ? servicio.estado : 'caido';
            const detalle = servicio.detalle || '';
            return `
                <div class="service-row" title="${esc(detalle)}">
                    <span class="status-dot ${estado}"></span>
                    <span class="service-name">${esc(servicio.servicio)}
                        <span class="service-meta">${servicio.latencia_ms != null ? `${esc(servicio.latencia_ms)} ms · ` : ''}${esc(relativeTime(servicio.ultima_comprobacion))}${detalle ? ` · ${esc(detalle)}` : ''}</span>
                    </span>
                    <span class="service-state ${estado}">${estados[estado]}</span>
                </div>`;
        }).join('');
    }

    /* ----------------------------------------------------------------------
       Descargas
       ---------------------------------------------------------------------- */

    async function downloadFile(path, fallbackName, boton) {
        if (boton) {
            boton.disabled = true;
            boton.classList.add('is-busy');
        }
        try {
            const response = await api(path);
            if (!response.ok) throw new Error(await errorMessage(response));
            const blob = await response.blob();
            const disposicion = response.headers.get('Content-Disposition') || '';
            const coincidencia = disposicion.match(/filename="?([^";]+)"?/i);
            const nombre = coincidencia ? coincidencia[1] : fallbackName;
            const url = URL.createObjectURL(blob);
            const enlace = document.createElement('a');
            enlace.href = url;
            enlace.download = nombre;
            document.body.appendChild(enlace);
            enlace.click();
            enlace.remove();
            window.setTimeout(() => URL.revokeObjectURL(url), 1800);
            toast(`Descarga generada: ${nombre}`, 'ok');
        } catch (error) {
            toast(error.message, 'error');
        } finally {
            if (boton) {
                boton.disabled = false;
                boton.classList.remove('is-busy');
            }
        }
    }

    /* ----------------------------------------------------------------------
       Actualización global
       ---------------------------------------------------------------------- */

    async function refreshAll() {
        const boton = $('#refresh-btn');
        boton.classList.add('is-busy');
        try {
            if (esInvitado()) {
                await loadZonas();
                await loadDistritos();
                renderGuestPanel();
                toast('Mapa público actualizado.', 'ok');
                return;
            }
            await loadZonas();
            await Promise.allSettled([
                loadDistritos(),
                loadPanel(),
                loadServicios(),
                loadNotificaciones(),
                loadPrioridad(),
                loadObservaciones(),
                loadAcciones(),
                canWrite() ? loadBitacora() : Promise.resolve()
            ]);
            await loadDiagnostico();
            toast('Datos actualizados.', 'ok');
        } catch (error) {
            toast(error.message, 'error');
        } finally {
            boton.classList.remove('is-busy');
        }
    }

    function startPolling() {
        if (state.polling.length) return;
        let ticks = 0;
        state.polling.push(window.setInterval(() => {
            ticks += 1;
            if (state.token) {
                loadNotificaciones();
                loadServicios();
            } else if (esInvitado() && ticks % 5 === 0) {
                // El mapa público se refresca cada 5 minutos, sin sesión.
                loadZonas();
            }
        }, 60000));
    }

    /* ----------------------------------------------------------------------
       Eventos
       ---------------------------------------------------------------------- */

    function wireGlobalEvents() {
        $('#login-form').addEventListener('submit', handleLogin);

        $$('[data-theme-toggle]').forEach((boton) => {
            boton.addEventListener('click', alternarTema);
        });
        $('#login-open-btn')?.addEventListener('click', openLogin);
        $('#login-close')?.addEventListener('click', closeLogin);
        $('#login-guest')?.addEventListener('click', closeLogin);
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && !$('#login').hidden) closeLogin();
        });

        $('#toggle-password').addEventListener('click', () => {
            const input = $('#login-password');
            const visible = input.type === 'text';
            input.type = visible ? 'password' : 'text';
            $('#toggle-password').setAttribute('aria-pressed', String(!visible));
            $('#toggle-password').setAttribute('aria-label', visible ? 'Mostrar contraseña' : 'Ocultar contraseña');
            input.focus();
        });

        $('#logout-btn').addEventListener('click', logout);
        $('#refresh-btn').addEventListener('click', refreshAll);

        $$('.view-btn').forEach((boton) => {
            boton.addEventListener('click', () => setVista(boton.dataset.view));
        });

        $('#notif-btn').addEventListener('click', () => {
            setDrawer(!$('#notif-drawer').classList.contains('is-open'));
        });
        $('#notif-close').addEventListener('click', () => setDrawer(false));
        $('#drawer-scrim').addEventListener('click', () => setDrawer(false));
        $('#notif-refresh').addEventListener('click', loadNotificaciones);

        $$('#notif-filter button').forEach((boton) => {
            boton.addEventListener('click', () => {
                state.notifFilter = boton.dataset.filter;
                $$('#notif-filter button').forEach((item) => item.classList.toggle('is-active', item === boton));
                renderNotificaciones();
            });
        });

        $('#services-btn').addEventListener('click', () => {
            const popover = $('#services-pop');
            const abrir = popover.hidden;
            popover.hidden = !abrir;
            $('#services-btn').setAttribute('aria-expanded', String(abrir));
            if (abrir) renderServicios();
        });
        $('#services-refresh').addEventListener('click', async (event) => {
            event.stopPropagation();
            const boton = event.currentTarget;
            boton.classList.add('is-busy');
            await loadServicios();
            boton.classList.remove('is-busy');
        });

        document.addEventListener('click', (event) => {
            if (!event.target.closest('.services-wrap')) {
                $('#services-pop').hidden = true;
                $('#services-btn').setAttribute('aria-expanded', 'false');
            }
        });

        $('#control-collapse').addEventListener('click', () => {
            const control = $('#layer-control');
            const colapsado = control.classList.toggle('is-collapsed');
            $('#control-collapse').setAttribute('aria-expanded', String(!colapsado));
            $('#control-collapse').setAttribute('title', colapsado ? 'Expandir capas' : 'Contraer capas');
        });

        if (window.matchMedia('(max-width: 900px)').matches) {
            $('#layer-control').classList.add('is-collapsed');
            $('#control-collapse').setAttribute('aria-expanded', 'false');
            $('#control-collapse').setAttribute('title', 'Expandir capas');
        }

        const compactQuery = window.matchMedia('(max-width: 900px)');
        const onCompactChange = (event) => {
            const control = $('#layer-control');
            control.classList.toggle('is-collapsed', event.matches);
            $('#control-collapse').setAttribute('aria-expanded', String(!event.matches));
            $('#control-collapse').setAttribute('title', event.matches ? 'Expandir capas' : 'Contraer capas');
            if (state.map) {
                state.mapHasFit = false;
                fitMapToData();
            }
        };
        if (compactQuery.addEventListener) compactQuery.addEventListener('change', onCompactChange);

        $$('.layer-row[data-layer]').forEach((boton) => {
            boton.addEventListener('click', () => {
                const nombre = boton.dataset.layer;
                setLayer(nombre, !state.capas[nombre]);
            });
        });

        $$('.risk-filter .chip-btn').forEach((boton) => {
            boton.addEventListener('click', () => {
                state.riskFilter = boton.dataset.risk;
                $$('.risk-filter .chip-btn').forEach((item) => item.classList.toggle('is-active', item === boton));
                renderRanking();
            });
        });

        $('#ranking-search').addEventListener('input', (event) => {
            state.rankingQuery = event.target.value;
            renderRanking();
        });

        $('#compare-clear').addEventListener('click', () => {
            state.compare = [];
            state.comparacion = null;
            state.compareError = '';
            renderRanking();
            renderComparison();
        });

        // Tabs del inspector
        $$('.tab[data-tab]').forEach((boton) => {
            boton.addEventListener('click', () => setTab(boton.dataset.tab));
        });

        $('.tabs').addEventListener('keydown', (event) => {
            const tabs = $$('.tab[data-tab]').filter((tab) => !tab.hidden);
            const actual = tabs.indexOf(document.activeElement);
            if (actual < 0) return;
            let destino = null;
            if (event.key === 'ArrowRight') destino = tabs[(actual + 1) % tabs.length];
            if (event.key === 'ArrowLeft') destino = tabs[(actual - 1 + tabs.length) % tabs.length];
            if (event.key === 'Home') destino = tabs[0];
            if (event.key === 'End') destino = tabs[tabs.length - 1];
            if (destino) {
                event.preventDefault();
                destino.focus();
                setTab(destino.dataset.tab);
            }
        });

        $$('.subtab').forEach((boton) => {
            boton.addEventListener('click', () => {
                state.opTab = boton.dataset.op;
                renderOperacion();
                if (state.opTab === 'bitacora' && canWrite()) loadBitacora(state.bitacoraZona);
            });
        });

        // Operación: formularios y acciones
        $('#op-body').addEventListener('submit', (event) => {
            event.preventDefault();
            if (event.target.id === 'obs-form') crearObservacion(event.target);
            if (event.target.id === 'acc-form') crearAccion(event.target);
        });

        $('#op-body').addEventListener('click', (event) => {
            const accion = event.target.closest('[data-accion-id]');
            if (accion) {
                actualizarAccion(accion.dataset.accionId, accion.dataset.nextState, accion);
            }
            if (event.target.closest('#bitacora-refresh')) {
                loadBitacora($('#bitacora-zona')?.value || '');
            }
        });

        $('#op-body').addEventListener('change', (event) => {
            if (event.target.id === 'bitacora-zona') {
                loadBitacora(event.target.value);
            }
        });

        // Simulador
        $('#sim-body').addEventListener('input', (event) => {
            if (event.target.matches('input[type="range"]')) updateSliderFill(event.target);
        });

        $('#sim-body').addEventListener('submit', (event) => {
            event.preventDefault();
            if (event.target.id === 'sim-form') runSimulation(event.target);
        });

        $('#sim-body').addEventListener('click', (event) => {
            if (event.target.closest('[data-sim-reset]')) {
                state.simResult = null;
                renderSimulador();
            }
        });

        // Asistente
        $('#chat-form').addEventListener('submit', (event) => {
            event.preventDefault();
            askAssistant($('#chat-input').value);
        });
        $('#chat-chips').addEventListener('click', (event) => {
            const chip = event.target.closest('[data-ask]');
            if (chip) askAssistant(chip.dataset.ask);
        });
        $('#chat-input').addEventListener('input', autoResizeChatInput);
        $('#chat-input').addEventListener('keydown', (event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                $('#chat-form').requestSubmit();
            }
        });

        // Usuarios
        $('#users-body').addEventListener('submit', (event) => {
            event.preventDefault();
            if (event.target.id === 'user-form') crearUsuario(event.target);
        });
        $('#users-body').addEventListener('click', (event) => {
            if (event.target.closest('#user-new')) {
                state.userFormOpen = !state.userFormOpen;
                renderUsuarios();
            }
            if (event.target.closest('#user-cancel')) {
                state.userFormOpen = false;
                renderUsuarios();
            }
            const toggle = event.target.closest('[data-user-toggle]');
            if (toggle) {
                const id = toggle.dataset.userToggle;
                const activo = toggle.dataset.userActive === 'true';
                actualizarUsuario(id, { activo: !activo }, toggle);
            }
        });
        $('#users-body').addEventListener('change', (event) => {
            const select = event.target.closest('[data-user-role]');
            if (select) {
                actualizarUsuario(select.dataset.userRole, { rol: select.value }, select);
            }
        });

        // Delegación global para elementos dinámicos
        document.addEventListener('click', (event) => {
            const zoneRow = event.target.closest('[data-zone]');
            if (zoneRow) {
                selectZone(zoneRow.dataset.zone, { fly: true });
                return;
            }

            const notif = event.target.closest('[data-notif-id]');
            if (notif) {
                marcarNotificacion(notif.dataset.notifId);
                return;
            }

            const sort = event.target.closest('[data-sort]');
            if (sort) {
                const key = sort.dataset.sort;
                if (state.sortKey === key) {
                    state.sortDir = state.sortDir === 'asc' ? 'desc' : 'asc';
                } else {
                    state.sortKey = key;
                    state.sortDir = key === 'comuna' ? 'asc' : 'desc';
                }
                renderRanking();
                return;
            }

            const compare = event.target.closest('[data-compare]');
            if (compare) {
                toggleCompare(compare.dataset.compare, compare.checked);
                return;
            }

            const openZone = event.target.closest('[data-open-zone]');
            if (openZone) {
                setVista('mapa');
                window.setTimeout(() => selectZone(openZone.dataset.openZone, { fly: true }), 90);
                return;
            }

            const download = event.target.closest('[data-download]');
            if (download) {
                if (download.dataset.download === 'csv') {
                    downloadFile('/api/exportar/csv', 'riesgo_meteorologico.csv', download);
                } else {
                    const zona = state.zonaActiva;
                    if (!zona) {
                        toast('Selecciona una zona para generar el reporte.', 'error');
                        return;
                    }
                    downloadFile(`/api/reportes/${encodeURIComponent(zona)}/pdf`, `reporte_riesgo_${zona}.pdf`, download);
                }
                return;
            }

            const ask = event.target.closest('[data-ask]');
            if (ask) {
                askAssistant(ask.dataset.ask);
                return;
            }

            const retry = event.target.closest('[data-retry]');
            if (retry) {
                if (retry.dataset.retry === 'prioridad') loadPrioridad();
                if (retry.dataset.retry === 'usuarios') loadUsuarios();
                if (retry.dataset.retry === 'comparar') loadComparison();
            }
        });

        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
                if ($('#notif-drawer').classList.contains('is-open')) setDrawer(false);
                $('#services-pop').hidden = true;
                $('#services-btn').setAttribute('aria-expanded', 'false');
            }
        });

        window.addEventListener('resize', () => {
            if (state.map) window.setTimeout(() => state.map.invalidateSize(), 120);
        });
    }

    function setTab(tab) {
        if (esInvitado()) return;
        if (tab === 'usuarios' && state.rol !== 'admin') return;
        state.tab = tab;
        $$('.tab[data-tab]').forEach((boton) => {
            const activo = boton.dataset.tab === tab;
            boton.classList.toggle('is-active', activo);
            boton.setAttribute('aria-selected', String(activo));
            boton.tabIndex = activo ? 0 : -1;
        });
        $$('.tabpanel').forEach((panel) => {
            panel.hidden = panel.id !== `panel-${tab}`;
        });

        if (tab === 'diagnostico' && (!state.explicacion || state.explicacion.zona !== state.zonaActiva)) {
            loadDiagnostico();
        }
        if (tab === 'simulador') {
            if (!state.explicacion) {
                $('#sim-body').innerHTML = '<p class="empty-note">Cargando escenario base…</p>';
                loadDiagnostico();
            } else {
                renderSimulador();
            }
        }
        if (tab === 'operacion') {
            renderOperacion();
            if (state.opTab === 'bitacora' && canWrite()) loadBitacora(state.bitacoraZona);
        }
        if (tab === 'usuarios') {
            if (!state.usuarios.length && !state.usersError) loadUsuarios();
            else renderUsuarios();
        }
        if (tab === 'asistente') {
            window.setTimeout(() => $('#chat-input')?.focus(), 80);
        }
    }
})();
