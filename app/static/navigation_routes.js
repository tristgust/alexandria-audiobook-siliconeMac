(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    }
    if (root) {
        root.AlexandriaRoutes = api;
    }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    const DESTINATIONS = Object.freeze({
        projects: Object.freeze({ legacyTab: 'setup', title: 'Projects' }),
        script: Object.freeze({ legacyTab: 'script', title: 'Script' }),
        cast: Object.freeze({ legacyTab: 'characters', title: 'Cast' }),
        produce: Object.freeze({ legacyTab: 'editor', title: 'Produce' }),
        export: Object.freeze({ legacyTab: 'audio', title: 'Export' }),
        library: Object.freeze({ legacyTab: 'designer', title: 'Library' }),
        voices: Object.freeze({ legacyTab: 'designer', title: 'Voices' }),
        templates: Object.freeze({ legacyTab: 'designer', title: 'Templates' }),
        settings: Object.freeze({ legacyTab: 'setup', title: 'Settings' }),
        more: Object.freeze({ legacyTab: 'speaker-management', title: 'More' }),
    });

    const TOOL_TO_TAB = Object.freeze({
        'advanced-character-operations': 'speaker-management',
        'voice-designer': 'designer',
        'audio-preparer': 'preparer',
        'dataset-builder': 'dataset-builder',
        'voice-training': 'training',
        'maintenance': 'project-recovery',
        'model-cache': 'project-recovery',
        'help-center': 'speaker-management',
    });

    const TAB_TO_TOOL = Object.freeze({
        'speaker-management': 'advanced-character-operations',
        designer: 'voice-designer',
        preparer: 'audio-preparer',
        'dataset-builder': 'dataset-builder',
        training: 'voice-training',
        'project-recovery': 'maintenance',
    });

    const LEGACY_ALIASES = Object.freeze({
        setup: Object.freeze({ destination: 'projects' }),
        projects: Object.freeze({ destination: 'projects' }),
        script: Object.freeze({ destination: 'script' }),
        characters: Object.freeze({ destination: 'cast' }),
        'voice-casting': Object.freeze({ destination: 'cast' }),
        'voice-projects': Object.freeze({ destination: 'cast' }),
        cast: Object.freeze({ destination: 'cast' }),
        editor: Object.freeze({ destination: 'produce' }),
        produce: Object.freeze({ destination: 'produce' }),
        audio: Object.freeze({ destination: 'export' }),
        result: Object.freeze({ destination: 'export' }),
        export: Object.freeze({ destination: 'export' }),
        library: Object.freeze({ destination: 'library' }),
        settings: Object.freeze({ destination: 'settings' }),
        'speaker-management': Object.freeze({
            destination: 'more',
            tool: 'advanced-character-operations',
        }),
        speakers: Object.freeze({
            destination: 'more',
            tool: 'advanced-character-operations',
        }),
        designer: Object.freeze({ destination: 'more', tool: 'voice-designer' }),
        preparer: Object.freeze({ destination: 'more', tool: 'audio-preparer' }),
        'dataset-builder': Object.freeze({ destination: 'more', tool: 'dataset-builder' }),
        training: Object.freeze({ destination: 'more', tool: 'voice-training' }),
        'project-recovery': Object.freeze({ destination: 'more', tool: 'maintenance' }),
        recovery: Object.freeze({ destination: 'more', tool: 'maintenance' }),
        models: Object.freeze({ destination: 'more', tool: 'model-cache' }),
        help: Object.freeze({ destination: 'more', tool: 'help-center' }),
        'help-center': Object.freeze({ destination: 'more', tool: 'help-center' }),
        more: Object.freeze({ destination: 'more' }),
    });

    const CONTEXT_KEYS = Object.freeze([
        'project',
        'character',
        'chunk',
        'chapter',
        'issue',
        'tool',
        'mode',
        'help',
        'topic',
        'return',
        'source',
        'filter',
        'search',
    ]);
    const CONTEXT_KEY_SET = new Set(CONTEXT_KEYS);
    const CONTROL_PATTERN = /[\u0000-\u001f\u007f]/;

    function safeValue(value, maximum = 512) {
        if (value === undefined || value === null) return null;
        const text = String(value).trim();
        if (!text || text.length > maximum || CONTROL_PATTERN.test(text)) {
            return null;
        }
        return text;
    }

    function normalizeContext(context) {
        const result = {};
        const source = context && typeof context === 'object' ? context : {};
        CONTEXT_KEYS.forEach(key => {
            const value = safeValue(source[key]);
            if (value !== null) result[key] = value;
        });
        return result;
    }

    function canonicalDestination(value) {
        const key = String(value || '').trim().toLocaleLowerCase();
        if (Object.prototype.hasOwnProperty.call(DESTINATIONS, key)) return key;
        const alias = LEGACY_ALIASES[key];
        return alias ? alias.destination : null;
    }

    function aliasDefinition(value) {
        const key = String(value || '').trim().toLocaleLowerCase();
        return LEGACY_ALIASES[key] || null;
    }

    function normalizeRoute(input) {
        if (typeof input === 'string') return parseHash(input);
        const source = input && typeof input === 'object' ? input : {};
        const requested = String(
            source.destination || source.route || source.tab || 'projects'
        ).trim().toLocaleLowerCase();
        const alias = aliasDefinition(requested);
        let destination = canonicalDestination(requested) || 'projects';
        const context = normalizeContext(source.context || source);
        if (
            destination === 'library'
            && (context.mode === 'voices' || context.mode === 'templates')
        ) {
            destination = context.mode;
            delete context.mode;
        }
        if (!context.tool && alias && alias.tool) context.tool = alias.tool;
        if (destination === 'more' && context.tool && !TOOL_TO_TAB[context.tool]) {
            delete context.tool;
        }
        const route = {
            destination,
            context,
            legacyTab: legacyTabForDestination(destination, context),
            title: DESTINATIONS[destination].title,
            aliasUsed: Boolean(alias && requested !== destination),
            unrecognized: !canonicalDestination(requested),
        };
        route.hash = serializeRoute(route);
        return route;
    }

    function parseHash(hashValue) {
        const rawValue = String(hashValue || '').replace(/^#/, '').trim();
        if (!rawValue) return normalizeRoute({ destination: 'projects' });
        const raw = rawValue.startsWith('/') ? rawValue.slice(1) : rawValue;
        const question = raw.indexOf('?');
        const rawPath = question >= 0 ? raw.slice(0, question) : raw;
        const rawQuery = question >= 0 ? raw.slice(question + 1) : '';
        const pathParts = rawPath
            .split('/')
            .map(value => {
                try {
                    return decodeURIComponent(value);
                } catch (error) {
                    return '';
                }
            })
            .filter(Boolean);
        const requested = String(pathParts[0] || 'projects').toLocaleLowerCase();
        const alias = aliasDefinition(requested);
        const destination = canonicalDestination(requested) || 'projects';
        const context = {};
        const params = new URLSearchParams(rawQuery);
        for (const [key, value] of params.entries()) {
            if (!CONTEXT_KEY_SET.has(key) || Object.prototype.hasOwnProperty.call(context, key)) {
                continue;
            }
            const safe = safeValue(value);
            if (safe !== null) context[key] = safe;
        }
        if (!context.tool && destination === 'more' && pathParts[1]) {
            const pathTool = safeValue(pathParts[1], 120);
            if (pathTool && TOOL_TO_TAB[pathTool]) context.tool = pathTool;
        }
        if (!context.tool && alias && alias.tool) context.tool = alias.tool;
        return normalizeRoute({ destination, context, _requested: requested });
    }

    function serializeRoute(routeValue) {
        const source = routeValue && typeof routeValue === 'object' ? routeValue : {};
        const destination = canonicalDestination(source.destination) || 'projects';
        const context = normalizeContext(source.context || source);
        const params = new URLSearchParams();
        CONTEXT_KEYS.forEach(key => {
            if (context[key] !== undefined) params.set(key, context[key]);
        });
        const query = params.toString();
        return `#/${encodeURIComponent(destination)}${query ? `?${query}` : ''}`;
    }

    function legacyTabForDestination(destinationValue, contextValue) {
        const destination = canonicalDestination(destinationValue) || 'projects';
        const context = normalizeContext(contextValue);
        if (destination === 'more' && context.tool && TOOL_TO_TAB[context.tool]) {
            return TOOL_TO_TAB[context.tool];
        }
        return DESTINATIONS[destination].legacyTab;
    }

    function routeForLegacyTab(tabValue, contextValue) {
        const tab = String(tabValue || '').trim().toLocaleLowerCase();
        const alias = aliasDefinition(tab);
        const context = normalizeContext(contextValue);
        if (alias && alias.tool && !context.tool) context.tool = alias.tool;
        return normalizeRoute({
            destination: alias ? alias.destination : canonicalDestination(tab) || 'projects',
            context,
        });
    }

    function routeForDestination(destinationValue, contextValue) {
        return normalizeRoute({
            destination: destinationValue,
            context: contextValue,
        });
    }

    function routeForLink(datasetValue) {
        const dataset = datasetValue && typeof datasetValue === 'object'
            ? datasetValue
            : {};
        const context = {};
        CONTEXT_KEYS.forEach(key => {
            const datasetKey = `route${key.charAt(0).toUpperCase()}${key.slice(1)}`;
            if (dataset[datasetKey] !== undefined) context[key] = dataset[datasetKey];
        });
        if (dataset.routeTool !== undefined) context.tool = dataset.routeTool;
        if (dataset.routeMode !== undefined) context.mode = dataset.routeMode;
        if (dataset.routeReturn !== undefined) context.return = dataset.routeReturn;
        const destination = dataset.route || dataset.destination;
        if (destination) return routeForDestination(destination, context);
        return routeForLegacyTab(dataset.tab, context);
    }

    function withContext(routeValue, changes) {
        const route = normalizeRoute(routeValue);
        return normalizeRoute({
            destination: route.destination,
            context: {
                ...route.context,
                ...normalizeContext(changes),
            },
        });
    }

    function withoutContext(routeValue, keys) {
        const route = normalizeRoute(routeValue);
        const context = { ...route.context };
        (Array.isArray(keys) ? keys : [keys]).forEach(key => delete context[key]);
        return normalizeRoute({ destination: route.destination, context });
    }

    function sameRoute(left, right) {
        return serializeRoute(normalizeRoute(left)) === serializeRoute(normalizeRoute(right));
    }

    return Object.freeze({
        DESTINATIONS,
        LEGACY_ALIASES,
        TOOL_TO_TAB,
        TAB_TO_TOOL,
        CONTEXT_KEYS,
        canonicalDestination,
        normalizeContext,
        normalizeRoute,
        parseHash,
        serializeRoute,
        legacyTabForDestination,
        routeForLegacyTab,
        routeForDestination,
        routeForLink,
        withContext,
        withoutContext,
        sameRoute,
    });
});
