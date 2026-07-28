(function () {
    'use strict';

    const routeApi = window.AlexandriaRoutes;
    if (!routeApi) return;

    const PROJECT_DESTINATIONS = new Set(['script', 'cast', 'produce', 'export']);
    const LIBRARY_DESTINATIONS = new Set(['library', 'voices', 'templates']);
    const VOICE_LIBRARY_KINDS = new Set([
        'built_in',
        'designed',
        'supplied_recording',
        'instruction_controlled',
        'adapter',
        'alias',
    ]);
    const STAGE_ORDER = ['script', 'cast', 'produce', 'export'];
    const STAGE_LABELS = {
        script: 'Script',
        cast: 'Cast',
        produce: 'Produce',
        export: 'Export',
    };
    const MORE_TOOL_COPY = {
        'advanced-character-operations': {
            title: 'Advanced identity operations',
            subtitle: 'Review guarded speaker and identity changes with exact undo.',
        },
        'voice-designer': {
            title: 'Voice designer',
            subtitle: 'Create and preview reusable Voice material without assigning it automatically.',
        },
        'audio-preparer': {
            title: 'Audio preparer',
            subtitle: 'Transcribe and segment owned recordings into reviewable material.',
        },
        'dataset-builder': {
            title: 'Dataset builder',
            subtitle: 'Inspect and package prepared Voice datasets.',
        },
        'voice-training': {
            title: 'Voice Lab',
            subtitle: 'Review reference banks, preparation state, and experimental training artifacts.',
        },
        'maintenance': {
            title: 'Maintenance',
            subtitle: 'Inspect recovery state, logs, repairs, and guarded technical actions.',
        },
        'model-cache': {
            title: 'Local model cache',
            subtitle: 'Inspect pinned model availability and explicit Download or Repair actions.',
        },
        'help-center': {
            title: 'Help Center',
            subtitle: 'Read versioned guidance bundled with Alexandria for offline use.',
        },
    };
    const GLOBAL_COPY = {
        projects: {
            title: 'Project Home',
            subtitle: 'Open an existing project or create a new one.',
        },
        library: {
            title: 'Library',
            subtitle: 'Inspect the active book, production audio, reusable Voice material, datasets, adapters, and finished outputs.',
        },
        voices: {
            title: 'Voices',
            subtitle: 'Browse reusable Voice material without changing the active Cast.',
        },
        templates: {
            title: 'Templates',
            subtitle: 'Start a project from Alexandria’s existing production presets.',
        },
        settings: {
            title: 'Settings',
            subtitle: 'Manage local generation, storage, and application preferences.',
        },
        more: {
            title: 'More',
            subtitle: 'Open specialist tools without losing the current project context.',
        },
    };

    const state = {
        route: routeApi.parseHash(window.location.hash),
        flow: null,
        catalog: null,
        projectFilter: 'active',
        projectSort: 'activity',
        projectSearch: '',
        projectRequest: 0,
        scriptReview: {
            entries: [],
            lifecycle: null,
            auditIssues: [],
            auditFingerprint: null,
            selectedIndex: 0,
            selectedIssueId: null,
            filter: 'all',
            query: '',
            visibleLimit: 80,
            request: 0,
            approving: false,
            approvalError: null,
        },
        cast: {
            aggregate: null,
            selectedId: null,
            search: '',
            filter: 'speaking_roles',
            request: 0,
            editing: false,
            dirty: false,
            saving: false,
            editVoiceName: null,
        },
        produce: {
            aggregate: null,
            chunks: [],
            selectedId: null,
            selectedChunk: null,
            filter: 'all',
            search: '',
            offset: 0,
            limit: 80,
            request: 0,
            polling: null,
        },
        export: {
            aggregate: null,
            format: 'mp3',
            plan: null,
            request: 0,
            planRequest: 0,
            polling: null,
            planning: false,
            building: false,
            chaptersExpanded: false,
            metadataInitialized: false,
        },
        library: {
            inventory: null,
            mode: null,
            selectedId: null,
            query: '',
            kind: '',
            stateFilter: '',
            request: 0,
            deleting: false,
        },
        voices: {
            request: 0,
            aggregate: null,
        },
        templates: {
            catalog: null,
            selectedId: null,
            query: '',
            scope: 'all',
            request: 0,
            saving: false,
            editingId: null,
        },
        settings: {
            payload: null,
            request: 0,
            saving: false,
            dirty: false,
        },
        maintenance: {
            recovery: null,
            models: null,
            memory: null,
            library: null,
            projects: null,
            migration: null,
            history: null,
            errors: {},
            request: 0,
            impact: null,
            actionRunning: false,
            impactTrigger: null,
        },
        more: {
            payload: null,
            query: '',
            request: 0,
            contextKey: null,
        },
        help: {
            topics: [],
            totalCount: 0,
            contextIndex: {},
            bundleVersion: null,
            selectedSlug: null,
            query: '',
            loadedQuery: null,
            request: 0,
            searchTimer: null,
        },
        newProject: {
            sourceFile: null,
            inspection: null,
            templateId: null,
            templateName: null,
            inspectionRequest: 0,
            inspecting: false,
            creating: false,
            completed: false,
            createdProject: null,
        },
    };

    function element(id) {
        return document.getElementById(id);
    }

    const CANONICAL_WORKSPACES = Object.freeze([
        Object.freeze({ id: 'project-home-workspace', destinations: ['projects'] }),
        Object.freeze({ id: 'script-review-workspace', destinations: ['script'] }),
        Object.freeze({ id: 'cast-workspace', destinations: ['cast'] }),
        Object.freeze({ id: 'produce-workspace', destinations: ['produce'] }),
        Object.freeze({ id: 'export-workspace', destinations: ['export'] }),
        Object.freeze({ id: 'library-workspace', destinations: ['library', 'voices'] }),
        Object.freeze({ id: 'templates-workspace', destinations: ['templates'] }),
        Object.freeze({ id: 'canonical-settings-workspace', destinations: ['settings'] }),
        Object.freeze({ id: 'canonical-maintenance-workspace', destinations: ['maintenance'] }),
        Object.freeze({ id: 'more-workspace', destinations: ['more'] }),
        Object.freeze({ id: 'help-center-workspace', destinations: ['help-center'] }),
    ]);

    function canonicalWorkspaceMode(route, definition) {
        if (definition.id === 'canonical-maintenance-workspace') {
            return route.destination === 'more'
                && ['maintenance', 'model-cache'].includes(route.context.tool)
                ? 'maintenance'
                : null;
        }
        if (definition.id === 'more-workspace') {
            return route.destination === 'more' && !route.context.tool ? 'more' : null;
        }
        if (definition.id === 'help-center-workspace') {
            return route.destination === 'more' && route.context.tool === 'help-center'
                ? 'help-center'
                : null;
        }
        return definition.destinations.includes(route.destination)
            ? route.destination
            : null;
    }

    function mountMaintenanceSpecialistTools() {
        const tools = [
            ['llm-profiles-panel', 'maintenance-stage-profiles-slot'],
            ['llm-runtime-panel', 'maintenance-runtime-slot'],
            ['promptSettings', 'maintenance-advanced-generation-slot'],
        ];
        tools.forEach(([sourceId, slotId]) => {
            const source = element(sourceId);
            const slot = element(slotId);
            if (!source || !slot || source.parentElement === slot) return;
            source.classList.add('maintenance-embedded-tool');
            source.open = true;
            slot.appendChild(source);
        });
    }

    function mountCanonicalWorkspaces() {
        const root = element('canonical-destination-root');
        if (!root || root.dataset.mounted === 'true') return root;
        mountMaintenanceSpecialistTools();
        CANONICAL_WORKSPACES.forEach(definition => {
            const workspace = element(definition.id);
            if (!workspace) return;
            workspace.dataset.canonicalPage = definition.destinations.join(' ');
            workspace.hidden = true;
            root.appendChild(workspace);
        });
        const maintenanceDialog = element('maintenance-impact-dialog');
        if (maintenanceDialog) root.appendChild(maintenanceDialog);
        root.dataset.mounted = 'true';
        return root;
    }

    function setCanonicalWorkspaceVisibility(route) {
        const root = mountCanonicalWorkspaces();
        if (!root) return false;
        let directRoute = false;
        CANONICAL_WORKSPACES.forEach(definition => {
            const workspace = element(definition.id);
            if (!workspace) return;
            const mode = canonicalWorkspaceMode(route, definition);
            workspace.hidden = !mode;
            workspace.toggleAttribute('inert', !mode);
            if (mode) directRoute = true;
        });
        root.hidden = !directRoute;
        root.dataset.destination = route.destination;
        document.body.classList.toggle('canonical-direct-route', directRoute);
        document.documentElement.dataset.alexandriaDestination = route.destination;
        return directRoute;
    }

    function escapeHtml(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function text(value, fallback = '—') {
        const normalized = String(value ?? '').trim();
        return normalized || fallback;
    }

    function titleCase(value) {
        return String(value || '')
            .replace(/[_-]+/g, ' ')
            .replace(/\b\w/g, character => character.toUpperCase());
    }

    function formatBytes(value) {
        const bytes = Number(value || 0);
        if (!Number.isFinite(bytes) || bytes <= 0) return '';
        if (bytes < 1024) return `${bytes} B`;
        if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
        if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
        return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
    }

    function formatActivity(value) {
        if (!value) return 'No saved activity';
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return 'Saved recently';
        const seconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000));
        if (seconds < 60) return 'Saved just now';
        const minutes = Math.floor(seconds / 60);
        if (minutes < 60) return `Saved ${minutes} min ago`;
        const hours = Math.floor(minutes / 60);
        if (hours < 24) return `Saved ${hours} hr ago`;
        const days = Math.floor(hours / 24);
        if (days < 7) return `Saved ${days} d ago`;
        return `Saved ${date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}`;
    }

    function showInlineStatus(message, status = 'info') {
        const region = element('canonical-shell-live');
        if (!region) return;
        region.dataset.state = status;
        region.textContent = message;
    }

    async function fetchJson(url, options = {}) {
        const response = await fetch(url, {
            credentials: 'same-origin',
            ...options,
            headers: {
                Accept: 'application/json',
                ...(options.headers || {}),
            },
        });
        let payload = null;
        try {
            payload = await response.json();
        } catch (error) {
            payload = null;
        }
        if (!response.ok) {
            const detail = payload && typeof payload.detail === 'object'
                ? payload.detail
                : payload;
            const message = detail?.message || detail?.detail || `Request failed (${response.status})`;
            const requestError = new Error(message);
            requestError.status = response.status;
            requestError.payload = payload;
            throw requestError;
        }
        return payload;
    }

    function setDestinationVisibility(route) {
        const destination = route.destination;
        const projectMode = PROJECT_DESTINATIONS.has(destination);
        document.body.classList.add('canonical-shell');
        document.body.dataset.destination = destination;
        document.body.dataset.shellMode = projectMode ? 'project' : 'global';

        const directRoute = setCanonicalWorkspaceVisibility(route);
        const libraryInventory = element('library-inventory-view');
        if (libraryInventory) libraryInventory.hidden = destination === 'templates';

        const legacyCharacterWorkspace = element('character-workspace');
        const legacyCastReturn = element('cast-return-summary');
        const legacySettings = element('legacy-settings-workspace');
        const recovery = element('recovery-center');
        [legacyCharacterWorkspace, legacyCastReturn, legacySettings, recovery]
            .filter(Boolean)
            .forEach(workspace => {
                workspace.hidden = true;
                workspace.setAttribute('inert', '');
            });

        if (directRoute) {
            document.querySelectorAll('.tab-content').forEach(tab => {
                tab.style.display = 'none';
                tab.hidden = true;
                tab.setAttribute('inert', '');
            });
        }

        const globalHeader = element('canonical-global-header');
        const projectHeader = element('canonical-project-header');
        const pageTitleRegion = element('canonical-page-title-region');
        if (globalHeader) globalHeader.hidden = projectMode;
        if (projectHeader) projectHeader.hidden = !projectMode;
        if (pageTitleRegion) pageTitleRegion.hidden = !projectMode;

        const currentProjectExists = Boolean(
            (state.catalog?.projects || []).find(project => project.current)
        );
        const homeNavigation = element('home-navigation');
        const projectNavigation = element('project-stage-navigation');
        const libraryNavigation = element('library-navigation');
        const libraryNavigationLabel = element('library-navigation-label');
        if (homeNavigation) homeNavigation.hidden = projectMode;
        if (libraryNavigation) {
            libraryNavigation.setAttribute(
                'aria-label',
                projectMode ? 'Global' : 'Library'
            );
        }
        if (libraryNavigationLabel) {
            libraryNavigationLabel.textContent = projectMode ? 'Global' : 'Library';
        }
        document.body.classList.toggle('project-stage-shell', projectMode);
        if (projectNavigation) {
            projectNavigation.hidden = !(
                projectMode
                || (destination === 'projects' && currentProjectExists)
            );
        }
        document.body.classList.toggle(
            'home-has-project-stages',
            destination === 'projects' && currentProjectExists
        );
        const headerSearch = element('project-header-search');
        if (headerSearch) headerSearch.hidden = destination !== 'projects';
    }

    function globalCopyForRoute(route) {
        if (route.destination === 'more' && route.context.tool) {
            return MORE_TOOL_COPY[route.context.tool] || GLOBAL_COPY.more;
        }
        return GLOBAL_COPY[route.destination] || GLOBAL_COPY.more;
    }

    function mountPrimaryAction(route) {
        const action = element('shell-primary-action');
        const slot = element(PROJECT_DESTINATIONS.has(route.destination)
            ? 'project-primary-action-slot'
            : 'global-primary-action-slot');
        if (action && slot && action.parentElement !== slot) slot.appendChild(action);
    }

    function setHeaderCopy(route) {
        const isProject = PROJECT_DESTINATIONS.has(route.destination);
        const globalHeading = element('shell-global-title');
        const globalSubtitle = element('shell-global-subtitle');
        const pageHeading = element('shell-page-title');
        const pageSubtitle = element('shell-page-subtitle');
        const globalMeta = globalCopyForRoute(route);

        updateContextualHelpActions(route);
        mountPrimaryAction(route);
        if (!isProject) {
            if (globalHeading) globalHeading.textContent = globalMeta.title;
            if (globalSubtitle) globalSubtitle.textContent = globalMeta.subtitle;
            renderPrimaryAction(route, null);
            return;
        }

        const projectTitle = element('shell-project-title');
        if (projectTitle) {
            const title = state.flow?.project?.name || 'Current project';
            projectTitle.childNodes[0].nodeValue = `${title} `;
            projectTitle.title = title;
        }
        const saved = element('shell-save-state');
        if (saved) {
            const activity = formatActivity(state.flow?.project?.latest_meaningful_activity);
            saved.textContent = activity.startsWith('Saved') ? `Last ${activity.toLocaleLowerCase()}` : activity;
        }
        if (pageHeading) pageHeading.textContent = STAGE_LABELS[route.destination] || titleCase(route.destination);
        const stage = state.flow?.stage_map?.[route.destination];
        if (pageSubtitle) {
            pageSubtitle.textContent = stage?.summary
                || `Continue the ${STAGE_LABELS[route.destination]} stage.`;
        }
        const workflow = element('shell-workflow-state');
        if (workflow) {
            workflow.textContent = stage?.state ? titleCase(stage.state) : 'Checking project';
            workflow.dataset.state = stage?.state || 'checking';
        }
        renderStageTracker(route, state.flow);
        renderPrimaryAction(route, stage);
    }

    function renderStageTracker(route, flow) {
        const tracker = element('shell-stage-tracker');
        if (!tracker) return;
        const currentIndex = STAGE_ORDER.indexOf(route.destination);
        tracker.innerHTML = STAGE_ORDER.map((key, index) => {
            const stage = flow?.stage_map?.[key] || {};
            const current = key === route.destination;
            const complete = stage.state === 'complete';
            const blocked = stage.state === 'blocked';
            const stateValue = current
                ? 'current'
                : complete
                    ? 'complete'
                    : blocked && index < currentIndex
                        ? 'blocked'
                        : 'future';
            const label = STAGE_LABELS[key];
            const canNavigate = complete || current || index <= STAGE_ORDER.indexOf(route.destination);
            const marker = complete
                ? '<i class="fas fa-check"></i>'
                : stateValue === 'blocked'
                    ? '<i class="fas fa-exclamation"></i>'
                    : '';
            const content = `
                <span class="shell-stage-number" aria-hidden="true">${marker}</span>
                <span class="shell-stage-label">${label}</span>
            `;
            return `<li data-state="${stateValue}">${canNavigate
                ? `<button type="button" class="shell-stage-link app-tab-link" data-tab="${routeApi.DESTINATIONS[key].legacyTab}" data-route="${key}" ${current ? 'aria-current="step"' : ''}>${content}</button>`
                : `<span class="shell-stage-link" aria-disabled="true">${content}</span>`}</li>`;
        }).join('');
    }

    function renderPrimaryAction(route, stage) {
        const action = element('shell-primary-action');
        if (!action) return;
        action.hidden = true;
        action.disabled = false;
        action.dataset.action = '';
        action.removeAttribute('aria-describedby');
        const scriptApprovalReason = element('script-approval-reason');
        if (scriptApprovalReason) {
            scriptApprovalReason.hidden = true;
            scriptApprovalReason.textContent = '';
        }

        if (route.destination === 'projects') {
            action.textContent = 'New Project';
            action.dataset.action = 'new-project';
            action.hidden = false;
            return;
        }
        if (route.destination === 'voices') {
            action.textContent = 'Create Voice';
            action.dataset.action = 'create-voice';
            action.hidden = false;
            return;
        }
        if (route.destination === 'templates') {
            action.textContent = 'New Template';
            action.dataset.action = 'new-template';
            action.hidden = false;
            return;
        }
        if (!PROJECT_DESTINATIONS.has(route.destination)) return;

        const stateValue = stage?.state;
        if (route.destination === 'script') {
            const nextAction = stage?.safe_next_action || {};
            const lifecycle = state.scriptReview.lifecycle;
            if (nextAction.id === 'generate_script' || lifecycle?.state === 'not_started') {
                action.textContent = 'Generate Script';
                action.dataset.action = 'script-generate';
                action.disabled = false;
            } else if (nextAction.id === 'review_imported_script' && !lifecycle?.artifact?.script_exists) {
                action.textContent = 'Review imported Script';
                action.dataset.action = 'script-import-review';
                action.disabled = false;
            } else if (lifecycle?.accepted || stateValue === 'complete') {
                action.textContent = 'Script approved';
                action.dataset.action = 'script-primary';
                action.disabled = true;
            } else {
                const approval = scriptApprovalState();
                action.textContent = state.scriptReview.approving ? 'Approving…' : 'Approve Script';
                action.dataset.action = 'script-primary';
                action.disabled = state.scriptReview.approving || !approval.canApprove;
                const reason = element('script-approval-reason');
                if (reason) {
                    reason.textContent = approval.reason || '';
                    reason.hidden = !approval.reason;
                }
                if (action.disabled && approval.reason) {
                    action.setAttribute('aria-describedby', 'script-approval-reason');
                }
            }
            action.hidden = false;
        } else if (route.destination === 'cast') {
            action.textContent = 'Continue to Produce';
            action.dataset.action = 'continue-produce';
            action.disabled = stateValue !== 'complete';
            action.hidden = false;
        } else if (route.destination === 'produce') {
            const aggregate = state.produce.aggregate;
            const process = aggregate?.process || {};
            const primary = aggregate?.primary_action || {};
            if (aggregate?.state === 'complete' || stateValue === 'complete') {
                action.textContent = 'Continue to Export';
                action.dataset.action = 'continue-export';
                action.disabled = false;
            } else {
                action.textContent = process.running
                    ? 'Generating audio…'
                    : text(primary.label, 'Generate missing and stale audio');
                action.dataset.action = 'produce-primary';
                action.disabled = Boolean(process.running) || !primary.id;
            }
            action.hidden = false;
        } else if (route.destination === 'export') {
            const aggregate = state.export.aggregate;
            const process = aggregate?.process || {};
            const plan = state.export.plan || aggregate?.plan || {};
            action.textContent = process.running || state.export.building
                ? 'Building audiobook…'
                : 'Build Audiobook';
            action.dataset.action = 'export-primary';
            action.disabled = Boolean(process.running || state.export.planning || state.export.building || !plan.safe_to_execute);
            action.hidden = false;
        }
    }

    function projectState(project) {
        if (project.availability_state !== 'available') {
            return {
                label: 'Unavailable',
                state: 'error',
                description: project.error || 'This project cannot be opened from its saved location.',
            };
        }
        if (project.compatibility_state && project.compatibility_state !== 'current') {
            return {
                label: 'Needs attention',
                state: 'warning',
                description: 'Review this project before continuing production.',
            };
        }
        if (project.completion_state === 'complete') {
            return {
                label: 'Complete',
                state: 'complete',
                description: 'The finished audiobook is ready in Export.',
            };
        }
        if (project.blocker_count > 0) {
            return {
                label: 'Needs attention',
                state: 'warning',
                description: project.stage_summary || 'A workflow issue needs review.',
            };
        }
        return {
            label: project.current ? 'In progress' : 'Ready to open',
            state: project.current ? 'current' : 'neutral',
            description: project.stage_summary || 'Continue at the next incomplete stage.',
        };
    }

    function projectActionLabel(project) {
        if (project.availability_state !== 'available') return 'Inspect';
        if (project.current) return project.blocker_count > 0 ? 'Resolve' : 'Resume';
        return 'Open Project';
    }

    function projectCover(project, className) {
        const title = text(project.source_title || project.name, 'Book');
        const words = title.split(/\s+/).filter(Boolean);
        const initials = words.slice(0, 3).map(word => word.charAt(0)).join('').toUpperCase();
        return `
            <div class="${className}" aria-hidden="true">
                <span class="project-cover-initials">${escapeHtml(initials || 'A')}</span>
                ${project.cover_url ? `<img class="project-cover-image" src="${escapeHtml(project.cover_url)}" alt="" loading="lazy">` : ''}
            </div>
        `;
    }

    function miniStageTracker(project) {
        const states = project.stage_states || {};
        return `
            <ol class="project-mini-tracker" aria-label="Project stages">
                ${STAGE_ORDER.map((key, index) => {
                    const stateValue = states[key] || 'not_started';
                    const complete = stateValue === 'complete';
                    const current = key === project.current_recommended_stage;
                    const stateName = complete ? 'complete' : current ? 'current' : stateValue === 'blocked' ? 'blocked' : 'future';
                    const marker = complete
                        ? '<i class="fas fa-check" aria-hidden="true"></i>'
                        : stateName === 'blocked'
                            ? '<i class="fas fa-exclamation" aria-hidden="true"></i>'
                            : '';
                    return `<li data-state="${stateName}"><span>${marker}</span><small>${STAGE_LABELS[key]}</small></li>`;
                }).join('')}
            </ol>
        `;
    }

    function projectStageLabel(project) {
        const key = project.current_recommended_stage;
        return key && STAGE_LABELS[key] ? STAGE_LABELS[key] : 'Project';
    }

    function filteredProjects() {
        const projects = Array.isArray(state.catalog?.projects) ? [...state.catalog.projects] : [];
        const query = state.projectSearch.trim().toLocaleLowerCase();
        const filtered = projects.filter(project => {
            if (state.projectFilter === 'active' && project.archive_state === 'archived') return false;
            if (state.projectFilter === 'attention' && !(project.blocker_count > 0 || project.availability_state !== 'available')) return false;
            if (state.projectFilter === 'complete' && project.completion_state !== 'complete') return false;
            if (state.projectFilter === 'archived' && project.archive_state !== 'archived') return false;
            if (!query) return true;
            return [project.name, project.source_title, project.source_author, project.source_filename]
                .some(value => String(value || '').toLocaleLowerCase().includes(query));
        });
        filtered.sort((left, right) => {
            if (state.projectSort === 'title') {
                return String(left.name || '').localeCompare(String(right.name || ''));
            }
            if (state.projectSort === 'stage') {
                return STAGE_ORDER.indexOf(left.current_recommended_stage)
                    - STAGE_ORDER.indexOf(right.current_recommended_stage);
            }
            return String(right.latest_meaningful_activity || '')
                .localeCompare(String(left.latest_meaningful_activity || ''));
        });
        return filtered;
    }

    function renderProjectHome() {
        const list = element('project-list');
        const continuation = element('project-continuation');
        const resultCount = element('project-result-count');
        if (!list || !continuation) return;

        const projects = filteredProjects();
        const activeProjects = (state.catalog?.projects || []).filter(project => project.archive_state !== 'archived');
        const continuationProject = activeProjects.find(project => project.current)
            || activeProjects.find(project => project.selected)
            || activeProjects[0];

        if (continuationProject) {
            const status = projectState(continuationProject);
            continuation.hidden = false;
            continuation.innerHTML = `
                <div class="project-continuation-heading">
                    <h2>Continue where you left off</h2>
                    <span aria-hidden="true"></span>
                </div>
                <div class="project-continuation-panel">
                    ${projectCover(continuationProject, 'project-cover-placeholder')}
                    <div class="project-continuation-copy">
                        <span class="canonical-kicker">Current audiobook</span>
                        <h2>${escapeHtml(text(continuationProject.source_title || continuationProject.name, 'Untitled project'))}</h2>
                        <p>${escapeHtml(text(continuationProject.source_author, continuationProject.name || 'Audiobook project'))}</p>
                        ${miniStageTracker(continuationProject)}
                        <div class="project-inline-state" data-state="${status.state}">
                            <span>${escapeHtml(formatActivity(continuationProject.latest_meaningful_activity))}</span>
                        </div>
                    </div>
                    <div class="project-continuation-next">
                        <span class="canonical-kicker">Next up</span>
                        <strong>${escapeHtml(projectStageLabel(continuationProject))}</strong>
                        <p>${escapeHtml(status.description)}</p>
                    </div>
                    <button type="button" class="btn btn-outline-secondary project-open-action" data-project-id="${escapeHtml(continuationProject.id)}">${escapeHtml(projectActionLabel(continuationProject))}</button>
                </div>
            `;
        } else {
            continuation.hidden = true;
            continuation.innerHTML = '';
        }

        if (resultCount) {
            resultCount.textContent = `${projects.length} ${projects.length === 1 ? 'project' : 'projects'}`;
        }

        if (!projects.length) {
            list.innerHTML = `
                <div class="canonical-empty-state" role="status">
                    <span class="canonical-empty-mark" aria-hidden="true"><i class="fas fa-book-open"></i></span>
                    <div>
                        <strong>No projects match this view</strong>
                        <p>Clear the search or choose another filter.</p>
                    </div>
                </div>
            `;
            return;
        }

        list.innerHTML = projects.map(project => {
            const status = projectState(project);
            return `
                <article class="project-row" data-project-id="${escapeHtml(project.id)}">
                    ${projectCover(project, 'project-row-cover')}
                    <div class="project-row-identity">
                        <h3>${escapeHtml(text(project.source_title || project.name, 'Untitled project'))}</h3>
                        <p>${escapeHtml(text(project.source_author, project.name || project.source_filename || 'Source not identified'))}</p>
                        <span>${escapeHtml(formatActivity(project.latest_meaningful_activity))}</span>
                        ${miniStageTracker(project)}
                    </div>
                    <div class="project-row-status" data-state="${status.state}">
                        <strong>${escapeHtml(status.label)}</strong>
                        <span>${escapeHtml(status.description)}</span>
                    </div>
                    <div class="project-row-stage">
                        <span class="canonical-kicker">Next</span>
                        <strong>${escapeHtml(projectStageLabel(project))}</strong>
                    </div>
                    <button type="button" class="btn btn-outline-secondary project-open-action" data-project-id="${escapeHtml(project.id)}">${escapeHtml(projectActionLabel(project))}</button>
                    <button type="button" class="canonical-icon-button project-more-action" data-project-id="${escapeHtml(project.id)}" aria-label="More actions for ${escapeHtml(text(project.name, 'project'))}" title="More project actions"><i class="fas fa-ellipsis"></i></button>
                </article>
            `;
        }).join('');
    }

    async function loadProjects(options = {}) {
        const request = ++state.projectRequest;
        const list = element('project-list');
        if (list && !options.silent) {
            list.innerHTML = `
                <div class="canonical-loading-list" aria-label="Loading projects">
                    ${Array.from({ length: 4 }, () => '<span></span>').join('')}
                </div>
            `;
        }
        try {
            const catalog = await fetchJson('/api/projects');
            if (request !== state.projectRequest) return;
            state.catalog = catalog;
            setDestinationVisibility(state.route);
            renderProjectHome();
        } catch (error) {
            if (request !== state.projectRequest) return;
            if (list) {
                list.innerHTML = `
                    <div class="canonical-error-state" role="alert">
                        <div>
                            <strong>Projects could not be loaded</strong>
                            <p>${escapeHtml(error.message)}</p>
                        </div>
                        <button type="button" class="btn btn-outline-secondary" id="project-retry">Retry</button>
                    </div>
                `;
            }
        }
    }

    async function openProject(projectId) {
        const project = (state.catalog?.projects || []).find(item => item.id === projectId);
        if (!project) return;
        const controls = document.querySelectorAll(`.project-open-action[data-project-id="${CSS.escape(projectId)}"]`);
        controls.forEach(control => {
            control.disabled = true;
            control.dataset.originalLabel = control.textContent;
            control.textContent = 'Opening…';
        });
        try {
            const result = await fetchJson(`/api/projects/${encodeURIComponent(projectId)}/open`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    expected_catalog_fingerprint: state.catalog.catalog_fingerprint,
                }),
            });
            state.catalog.catalog_fingerprint = result.catalog_fingerprint || state.catalog.catalog_fingerprint;
            const activation = result.activation || result;
            if (activation.state === 'current') {
                const destination = activation.native_destination
                    || project.current_recommended_stage
                    || 'script';
                window.AlexandriaNavigation?.navigate(destination, { project: project.id });
                return;
            }
            throw new Error(
                activation.message
                || 'Alexandria did not activate the selected project.'
            );
        } catch (error) {
            showInlineStatus(`Could not open project. ${error.message}`, 'error');
        } finally {
            controls.forEach(control => {
                control.disabled = false;
                control.textContent = control.dataset.originalLabel || 'Open';
                delete control.dataset.originalLabel;
            });
        }
    }

    function setupProjectHome() {
        element('project-search')?.addEventListener('input', event => {
            state.projectSearch = event.target.value;
            renderProjectHome();
        });
        element('project-sort')?.addEventListener('change', event => {
            state.projectSort = event.target.value;
            renderProjectHome();
        });
        element('project-filter')?.addEventListener('change', event => {
            state.projectFilter = event.target.value;
            renderProjectHome();
        });
        document.addEventListener('error', event => {
            if (event.target instanceof HTMLImageElement && event.target.classList.contains('project-cover-image')) {
                event.target.remove();
            }
        }, true);
        element('project-list')?.addEventListener('click', event => {
            const retry = event.target.closest('#project-retry');
            if (retry) {
                loadProjects();
                return;
            }
            const action = event.target.closest('.project-open-action');
            if (action) openProject(action.dataset.projectId);
        });
        element('project-continuation')?.addEventListener('click', event => {
            const action = event.target.closest('.project-open-action');
            if (action) openProject(action.dataset.projectId);
        });
    }

    function scriptIssueType(issue) {
        const value = [issue?.code, issue?.title, issue?.message, issue?.explanation]
            .filter(Boolean)
            .join(' ')
            .toLocaleLowerCase();
        if (/delivery|direction|instruct|prosody|pause/.test(value)) return 'delivery_direction';
        if (/speaker|attribution|dialogue|voice label/.test(value)) return 'uncertain_speaker';
        return 'source_mismatch';
    }

    function scriptIssuePresentation(type) {
        if (type === 'uncertain_speaker') {
            return {
                label: 'Uncertain speaker',
                title: 'Speaker attribution requires review',
                icon: 'fa-user-pen',
                action: 'Review speaker correction',
                legacyAction: 'chatgpt',
            };
        }
        if (type === 'delivery_direction') {
            return {
                label: 'Delivery direction',
                title: 'Delivery direction requires review',
                icon: 'fa-feather-pointed',
                action: 'Review delivery correction',
                legacyAction: 'chatgpt',
            };
        }
        return {
            label: 'Source mismatch',
            title: 'Script text does not match the source',
            icon: 'fa-shield-halved',
            action: 'Replace mismatched Script',
            legacyAction: 'import',
        };
    }

    function scriptIssueEntryIndex(issue) {
        const context = issue?.context && typeof issue.context === 'object' ? issue.context : {};
        const candidates = [
            issue?.entry_index,
            context.entry_index,
            Array.isArray(issue?.output_indices) ? issue.output_indices[0] : null,
            Array.isArray(context.output_indices) ? context.output_indices[0] : null,
        ];
        const value = candidates.find(candidate => (
            candidate !== null
            && candidate !== undefined
            && candidate !== ''
            && Number.isInteger(Number(candidate))
        ));
        if (value === undefined || value === null) return null;
        const index = Number(value);
        return index >= 0 && index < state.scriptReview.entries.length ? index : null;
    }

    function normalizedScriptIssues() {
        const review = state.scriptReview;
        const raw = [
            ...(Array.isArray(review.lifecycle?.blockers) ? review.lifecycle.blockers : []),
            ...(Array.isArray(review.auditIssues) ? review.auditIssues : []),
        ];
        const seen = new Set();
        return raw.map((issue, position) => {
            const type = scriptIssueType(issue);
            const entryIndex = scriptIssueEntryIndex(issue);
            const context = issue?.context && typeof issue.context === 'object' ? issue.context : {};
            const sourceText = text(issue?.source_text || context.source_text, 'Source passage unavailable.');
            const outputText = text(issue?.output_text || context.output_text, entryIndex === null
                ? 'No single Script entry is associated with this issue.'
                : review.entries[entryIndex]?.text);
            const seed = [issue?.code, entryIndex, sourceText, outputText].join('|');
            let id = `script-issue-${seed.replace(/[^a-z0-9]+/gi, '-').replace(/^-|-$/g, '').slice(0, 96) || position}`;
            while (seen.has(id)) id = `${id}-${position}`;
            seen.add(id);
            const presentation = scriptIssuePresentation(type);
            return {
                id,
                type,
                entryIndex,
                code: text(issue?.code, 'script_review_issue'),
                label: presentation.label,
                title: text(issue?.title || issue?.message, presentation.title),
                explanation: text(issue?.explanation || issue?.message, 'Review this issue before approving the Script.'),
                sourceText,
                outputText,
                presentation,
                blocking: issue?.blocking !== false && issue?.severity !== 'warning',
                raw: issue,
            };
        });
    }

    function scriptIssueCounts(issues = normalizedScriptIssues()) {
        return issues.reduce((counts, issue) => {
            counts.all += 1;
            counts[issue.type] += 1;
            return counts;
        }, { all: 0, uncertain_speaker: 0, delivery_direction: 0, source_mismatch: 0 });
    }

    function scriptApprovalState() {
        const lifecycle = state.scriptReview.lifecycle;
        if (!lifecycle) return { canApprove: false, reason: 'Wait for the current Script review to load.' };
        if (lifecycle.accepted) return { canApprove: false, reason: 'This Script version is already approved.' };
        if (lifecycle.process?.running) return { canApprove: false, reason: 'Wait for Script generation to finish before approval.' };
        if (lifecycle.process?.resumable) return { canApprove: false, reason: 'Resume or discard saved Script generation before approval.' };
        if (!lifecycle.source_available) return { canApprove: false, reason: 'Select a readable source before approving the Script.' };
        if (!lifecycle.artifact?.script_exists || !lifecycle.artifact?.metadata_exists) {
            return { canApprove: false, reason: 'Generate or import a complete Script before approval.' };
        }
        const issues = normalizedScriptIssues().filter(issue => issue.blocking);
        if (issues.length) {
            return {
                canApprove: false,
                reason: `Resolve ${issues.length} blocking issue${issues.length === 1 ? '' : 's'} before approval.`,
            };
        }
        const fingerprints = lifecycle.fingerprints || {};
        if (!fingerprints.script || !fingerprints.metadata || !fingerprints.source) {
            return { canApprove: false, reason: 'Current Script and source fingerprints are not available for safe approval.' };
        }
        return { canApprove: true, reason: '' };
    }

    function scriptIssueForEntry(index, issues = normalizedScriptIssues()) {
        return issues.find(issue => issue.entryIndex === index) || null;
    }

    function selectedScriptIssue(issues = normalizedScriptIssues()) {
        const review = state.scriptReview;
        if (review.selectedIssueId === '__none__') return null;
        const selected = issues.find(issue => issue.id === review.selectedIssueId)
            || scriptIssueForEntry(review.selectedIndex, issues)
            || (review.selectedIssueId === null ? issues[0] : null)
            || null;
        if (selected) review.selectedIssueId = selected.id;
        return selected;
    }

    function filteredScriptIssues(issues = normalizedScriptIssues()) {
        const review = state.scriptReview;
        const query = review.query.trim().toLocaleLowerCase();
        return issues.filter(issue => {
            if (review.filter !== 'all' && issue.type !== review.filter) return false;
            if (!query) return true;
            const entry = issue.entryIndex === null ? null : review.entries[issue.entryIndex];
            return [
                issue.label,
                issue.title,
                issue.explanation,
                issue.sourceText,
                issue.outputText,
                entry?.speaker,
                entry?.text,
                entry?.instruct,
            ].some(value => String(value || '').toLocaleLowerCase().includes(query));
        });
    }

    function filteredScriptEntries() {
        const review = state.scriptReview;
        const query = review.query.trim().toLocaleLowerCase();
        const issues = normalizedScriptIssues();
        return review.entries
            .map((entry, index) => ({ entry, index, issue: scriptIssueForEntry(index, issues) }))
            .filter(({ entry, issue }) => {
                if (review.filter !== 'all' && issue?.type !== review.filter) return false;
                if (!query) return true;
                return [entry.speaker, entry.text, entry.instruct, issue?.sourceText]
                    .some(value => String(value || '').toLocaleLowerCase().includes(query));
            });
    }

    function scriptIssuePosition(issue, issues = filteredScriptIssues()) {
        return issue ? issues.findIndex(candidate => candidate.id === issue.id) : -1;
    }

    function selectScriptIssue(issue, { open = false, scroll = false } = {}) {
        if (!issue) return;
        state.scriptReview.selectedIssueId = issue.id;
        if (issue.entryIndex !== null) state.scriptReview.selectedIndex = issue.entryIndex;
        renderScriptEntryList();
        renderScriptInspector({ open });
        if (scroll && issue.entryIndex !== null) {
            document.querySelector(`[data-script-entry-index="${issue.entryIndex}"]`)
                ?.scrollIntoView({ block: 'center' });
        }
    }

    function moveScriptIssue(direction) {
        const issues = filteredScriptIssues();
        const current = selectedScriptIssue(issues);
        const position = scriptIssuePosition(current, issues);
        const nextPosition = Math.max(0, Math.min(issues.length - 1, position + direction));
        if (issues[nextPosition]) selectScriptIssue(issues[nextPosition], { scroll: true });
    }

    function renderScriptInspector({ open = false } = {}) {
        const review = state.scriptReview;
        const entries = review.entries;
        const selectedIndex = Math.max(0, Math.min(review.selectedIndex, entries.length - 1));
        review.selectedIndex = selectedIndex;
        const entry = entries[selectedIndex];
        const issues = normalizedScriptIssues();
        const filteredIssues = filteredScriptIssues(issues);
        const issue = selectedScriptIssue(issues);
        const inspector = element('script-review-inspector');
        const kicker = element('script-inspector-kicker');
        const issueType = element('script-inspector-issue-type');
        const speaker = element('script-inspector-speaker');
        const index = element('script-inspector-index');
        const explanation = element('script-inspector-explanation');
        const scriptLabel = element('script-inspector-script-label');
        const textValue = element('script-inspector-text');
        const directionSection = element('script-inspector-direction-section');
        const direction = element('script-inspector-direction');
        const sourceSection = element('script-inspector-source-section');
        const sourceValue = element('script-inspector-source-note');
        const stateValue = element('script-inspector-state');
        const actions = element('script-inspector-actions');
        const primary = element('script-issue-primary-action');
        const previous = element('script-entry-previous');
        const next = element('script-entry-next');
        const footerPrevious = element('script-issue-previous');
        const footerNext = element('script-issue-next');

        if (!entry && !issue) {
            if (kicker) kicker.textContent = 'Selected entry';
            if (issueType) issueType.hidden = true;
            if (speaker) speaker.textContent = 'No entry selected';
            if (index) index.textContent = '—';
            if (explanation) explanation.hidden = true;
            if (textValue) textValue.textContent = 'No Script entries match the current filters.';
            if (direction) direction.textContent = '—';
            if (sourceSection) sourceSection.hidden = true;
            if (actions) actions.hidden = true;
            [previous, next, footerPrevious, footerNext].forEach(button => { if (button) button.disabled = true; });
            return;
        }

        if (issue) {
            const presentation = issue.presentation;
            const position = scriptIssuePosition(issue, filteredIssues);
            if (kicker) kicker.textContent = 'Selected issue';
            if (issueType) {
                issueType.hidden = false;
                issueType.innerHTML = `<i class="fas ${presentation.icon}" aria-hidden="true"></i>${escapeHtml(issue.label)}`;
            }
            if (speaker) speaker.textContent = issue.title;
            if (index) {
                const location = issue.entryIndex === null ? 'Script-wide issue' : `Entry ${issue.entryIndex + 1}`;
                index.textContent = `${location} · ${Math.max(position + 1, 1)} of ${filteredIssues.length || issues.length}`;
            }
            if (explanation) {
                explanation.hidden = false;
                explanation.textContent = issue.explanation;
            }
            if (scriptLabel) scriptLabel.textContent = 'Script';
            if (textValue) textValue.textContent = issue.outputText;
            if (directionSection) directionSection.hidden = !entry?.instruct;
            if (direction) direction.textContent = text(entry?.instruct, 'No delivery direction recorded.');
            if (sourceSection) sourceSection.hidden = false;
            if (sourceValue) sourceValue.textContent = issue.sourceText;
            if (stateValue) {
                stateValue.dataset.state = 'warning';
                stateValue.innerHTML = '<i class="fas fa-triangle-exclamation" aria-hidden="true"></i><span>Blocking issue remains unresolved</span>';
            }
            if (actions) actions.hidden = false;
            if (primary) {
                primary.textContent = presentation.action;
                primary.dataset.scriptLegacyAction = presentation.legacyAction;
            }
            const first = position <= 0;
            const last = position < 0 || position >= filteredIssues.length - 1;
            [previous, footerPrevious].forEach(button => { if (button) button.disabled = first; });
            [next, footerNext].forEach(button => { if (button) button.disabled = last; });
        } else {
            if (kicker) kicker.textContent = 'Selected entry';
            if (issueType) issueType.hidden = true;
            if (speaker) speaker.textContent = text(entry?.speaker, 'NARRATOR');
            if (index) index.textContent = `Entry ${selectedIndex + 1} of ${entries.length}`;
            if (explanation) explanation.hidden = true;
            if (scriptLabel) scriptLabel.textContent = 'Script text';
            if (textValue) textValue.textContent = text(entry?.text, 'No text');
            if (directionSection) directionSection.hidden = false;
            if (direction) direction.textContent = text(entry?.instruct, 'No delivery direction recorded.');
            if (sourceSection) sourceSection.hidden = true;
            if (actions) actions.hidden = true;
            if (stateValue) {
                stateValue.dataset.state = 'neutral';
                stateValue.innerHTML = '<i class="fas fa-circle-check" aria-hidden="true"></i><span>No entry-specific issue recorded</span>';
            }
            [previous, footerPrevious].forEach(button => { if (button) button.disabled = selectedIndex <= 0; });
            [next, footerNext].forEach(button => { if (button) button.disabled = selectedIndex >= entries.length - 1; });
        }
        if (open && window.innerWidth < 1180) inspector?.classList.add('is-open');
    }

    function renderScriptReviewControls() {
        const issues = normalizedScriptIssues();
        const counts = scriptIssueCounts(issues);
        document.querySelectorAll('[data-script-filter]').forEach(button => {
            const filter = button.dataset.scriptFilter;
            const count = counts[filter] || 0;
            const countElement = button.querySelector('[data-script-filter-count]');
            if (countElement) countElement.textContent = count.toLocaleString();
            button.classList.toggle('is-active', state.scriptReview.filter === filter);
            button.setAttribute('aria-pressed', String(state.scriptReview.filter === filter));
            button.disabled = filter !== 'all' && count === 0;
            const label = button.dataset.scriptFilterLabel || button.childNodes[0]?.textContent?.trim() || 'Issue';
            button.setAttribute('aria-label', `${label}, ${count} unresolved`);
        });

        const summary = element('script-blocker-summary');
        const summaryTitle = element('script-blocker-summary-title');
        const summaryCounts = element('script-blocker-summary-counts');
        const blocking = issues.filter(issue => issue.blocking);
        if (summary) summary.hidden = blocking.length === 0;
        if (summaryTitle) {
            summaryTitle.textContent = `${blocking.length} blocking issue${blocking.length === 1 ? '' : 's'} remaining`;
        }
        if (summaryCounts) {
            summaryCounts.innerHTML = ['uncertain_speaker', 'delivery_direction', 'source_mismatch']
                .filter(type => counts[type] > 0)
                .map(type => {
                    const presentation = scriptIssuePresentation(type);
                    return `<span><i class="fas ${presentation.icon}" aria-hidden="true"></i>${escapeHtml(presentation.label)} <strong>${counts[type]}</strong></span>`;
                }).join('');
        }

        const filteredIssues = filteredScriptIssues(issues);
        const issue = selectedScriptIssue(issues);
        const position = scriptIssuePosition(issue, filteredIssues);
        const issuePosition = element('script-issue-position');
        if (issuePosition) {
            issuePosition.textContent = filteredIssues.length
                ? `${Math.max(position + 1, 1)} of ${filteredIssues.length}`
                : 'No unresolved issues';
        }
    }

    function renderScriptEntryList() {
        const list = element('script-entry-list');
        if (!list) return;
        const matches = filteredScriptEntries();
        if (matches.length && !matches.some(item => item.index === state.scriptReview.selectedIndex)) {
            state.scriptReview.selectedIndex = matches[0].index;
            state.scriptReview.selectedIssueId = matches[0].issue?.id || null;
        }
        const visibleEntries = matches.slice(0, state.scriptReview.visibleLimit);
        list.innerHTML = visibleEntries.length
            ? visibleEntries.map(({ entry, index, issue }) => {
                const selected = index === state.scriptReview.selectedIndex;
                const issueLabel = issue ? `<span class="script-entry-issue"><i class="fas fa-triangle-exclamation" aria-hidden="true"></i><span class="visually-hidden">${escapeHtml(issue.label)}. </span></span>` : '';
                return `
                    <button type="button" class="script-entry-row${issue ? ' has-issue' : ''}" role="option" aria-selected="${selected}" data-script-entry-index="${index}"${issue ? ` data-script-issue-id="${escapeHtml(issue.id)}"` : ''}>
                        <span class="script-entry-number">${issueLabel}${index + 1}</span>
                        <span class="script-entry-speaker">${escapeHtml(text(entry.speaker, 'NARRATOR'))}</span>
                        <span class="script-entry-copy">
                            <span class="script-entry-text">${escapeHtml(text(entry.text, 'No text'))}</span>
                            <span class="script-entry-direction"><span class="visually-hidden">Delivery direction: </span>${escapeHtml(text(entry.instruct, 'No delivery direction recorded.'))}</span>
                        </span>
                        <span class="script-entry-menu" aria-hidden="true"><i class="fas fa-ellipsis"></i></span>
                    </button>
                `;
            }).join('')
            : `
                <div class="canonical-empty-state">
                    <div>
                        <strong>No Script entries match these filters</strong>
                        <p>Clear the issue filter or search to restore the Script.</p>
                    </div>
                    <button type="button" class="btn btn-outline-secondary" id="script-clear-filters">Clear filters</button>
                </div>
            `;
        const count = element('script-list-count');
        if (count) {
            const first = visibleEntries.length ? visibleEntries[0].index + 1 : 0;
            const last = visibleEntries.length ? visibleEntries[visibleEntries.length - 1].index + 1 : 0;
            count.textContent = visibleEntries.length
                ? `Showing ${first.toLocaleString()}–${last.toLocaleString()} of ${state.scriptReview.entries.length.toLocaleString()} entries`
                : `0 of ${state.scriptReview.entries.length.toLocaleString()} entries`;
        }
        const more = element('script-load-more');
        if (more) more.hidden = visibleEntries.length >= matches.length;
        renderScriptReviewControls();
        renderScriptInspector();
    }

    function renderScriptReviewMetadata() {
        const lifecycle = state.scriptReview.lifecycle || {};
        const source = state.flow?.source || {};
        const provenance = lifecycle.provenance || {};
        const sourceTitle = element('script-review-source-title');
        const sourceMeta = element('script-review-source-meta');
        const total = element('script-review-entry-total');
        if (sourceTitle) sourceTitle.textContent = text(source.title || state.flow?.project?.name, 'Current source');
        if (sourceMeta) {
            sourceMeta.textContent = [
                source.filename,
                provenance.model_name,
                provenance.provenance_status ? titleCase(provenance.provenance_status) : null,
            ].filter(Boolean).join(' · ');
        }
        if (total) total.textContent = `${state.scriptReview.entries.length.toLocaleString()} entries`;
        const method = element('script-provenance-method');
        const sourceStatus = element('script-provenance-source');
        const entries = element('script-provenance-entries');
        const version = element('script-provenance-version');
        if (method) method.textContent = titleCase(lifecycle.generation_method || provenance.method || 'unknown');
        if (sourceStatus) sourceStatus.textContent = titleCase(provenance.provenance_status || (source.available ? 'available' : 'unavailable'));
        if (entries) entries.textContent = state.scriptReview.entries.length.toLocaleString();
        if (version) version.textContent = lifecycle.accepted_version_id || 'Not approved';

        const issues = normalizedScriptIssues();
        const blocking = issues.filter(issue => issue.blocking);
        const pageSubtitle = element('shell-page-subtitle');
        const workflow = element('shell-workflow-state');
        if (lifecycle.accepted) {
            if (pageSubtitle) pageSubtitle.textContent = 'Approved — the current Script is ready for Cast.';
            if (workflow) {
                workflow.textContent = 'Approved';
                workflow.dataset.state = 'complete';
            }
        } else if (blocking.length) {
            if (pageSubtitle) pageSubtitle.textContent = `Generation complete — ${blocking.length} issue${blocking.length === 1 ? '' : 's'} require review before approval.`;
            if (workflow) {
                workflow.textContent = 'Review required';
                workflow.dataset.state = 'review_required';
            }
        } else {
            if (pageSubtitle) pageSubtitle.textContent = provenance.provenance_status === 'unverified'
                ? 'Review complete — approval will verify this imported Script against the selected source.'
                : 'Review complete — ready for approval.';
            if (workflow) {
                workflow.textContent = 'Ready for approval';
                workflow.dataset.state = 'ready';
            }
        }

        const notice = element('script-review-notice');
        const noticeTitle = element('script-review-notice-title');
        const noticeCopy = element('script-review-notice-copy');
        if (notice && noticeTitle && noticeCopy) {
            notice.hidden = !state.scriptReview.approvalError;
            if (state.scriptReview.approvalError) {
                notice.dataset.state = 'error';
                noticeTitle.textContent = 'Script could not be approved';
                noticeCopy.textContent = state.scriptReview.approvalError;
            }
        }
        renderScriptReviewControls();
        renderPrimaryAction(state.route, state.flow?.stage_map?.script || null);
    }

    async function loadScriptReview({ force = false } = {}) {
        const review = state.scriptReview;
        if (force) {
            review.entries = [];
            review.lifecycle = null;
            review.approvalError = null;
        }
        const request = ++review.request;
        const loading = element('script-review-loading');
        const content = element('script-review-content');
        if (!force && review.entries.length && review.lifecycle) {
            renderScriptReviewMetadata();
            renderScriptEntryList();
            return;
        }
        if (loading) {
            loading.hidden = false;
            loading.innerHTML = '<span class="spinner-border spinner-border-sm" aria-hidden="true"></span><span>Loading the authoritative Script…</span>';
        }
        if (content) content.hidden = true;
        try {
            const [lifecycle, entries] = await Promise.all([
                fetchJson('/api/script_lifecycle/status'),
                fetchJson('/api/annotated_script'),
            ]);
            if (request !== review.request) return;
            review.lifecycle = lifecycle;
            review.entries = Array.isArray(entries) ? entries : [];
            const currentScriptFingerprint = lifecycle.fingerprints?.script || null;
            const auditBecameStale = Boolean(
                review.auditFingerprint
                && review.auditFingerprint !== currentScriptFingerprint
            );
            if (lifecycle.accepted || auditBecameStale) {
                review.auditIssues = [];
                review.auditFingerprint = null;
                review.selectedIssueId = null;
            }
            review.selectedIndex = Math.max(0, Math.min(review.selectedIndex, review.entries.length - 1));
            review.visibleLimit = 80;
            renderScriptReviewMetadata();
            renderScriptEntryList();
            if (content) content.hidden = false;
            if (loading) loading.hidden = true;
        } catch (error) {
            if (request !== review.request) return;
            if (loading) {
                loading.hidden = false;
                loading.innerHTML = `
                    <div class="canonical-error-state" role="alert">
                        <div>
                            <strong>Script could not be loaded</strong>
                            <p>${escapeHtml(error.message)}</p>
                        </div>
                        <button type="button" class="btn btn-outline-secondary" id="script-review-retry">Retry</button>
                    </div>
                `;
            }
        }
    }

    async function approveCurrentScript() {
        const review = state.scriptReview;
        const lifecycle = review.lifecycle;
        const approval = scriptApprovalState();
        if (!lifecycle || !approval.canApprove || review.approving) return;
        review.approving = true;
        review.approvalError = null;
        renderScriptReviewMetadata();
        try {
            const fingerprints = lifecycle.fingerprints || {};
            await fetchJson('/api/script_lifecycle/accept', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    expected_script_fingerprint: fingerprints.script,
                    expected_metadata_fingerprint: fingerprints.metadata,
                    expected_source_fingerprint: fingerprints.source,
                    expected_state_fingerprint: lifecycle.state_fingerprint || null,
                }),
            });
            review.auditIssues = [];
            review.auditFingerprint = null;
            review.selectedIssueId = null;
            showInlineStatus('Script approved. Cast is now available.', 'success');
            await Promise.all([
                loadScriptReview({ force: true }),
                loadFlow(state.route),
            ]);
        } catch (error) {
            const detail = error.payload?.detail && typeof error.payload.detail === 'object'
                ? error.payload.detail
                : {};
            const context = detail.context && typeof detail.context === 'object'
                ? detail.context
                : {};
            if (detail.code === 'script_acceptance_blocked' && Array.isArray(context.blocking_issues)) {
                review.auditIssues = context.blocking_issues;
                review.auditFingerprint = lifecycle.fingerprints?.script || null;
                const issues = normalizedScriptIssues();
                review.selectedIssueId = issues[0]?.id || null;
                if (issues[0]?.entryIndex !== null && issues[0]?.entryIndex !== undefined) {
                    review.selectedIndex = issues[0].entryIndex;
                }
                review.approvalError = null;
                showInlineStatus(
                    `Approval found ${issues.length} blocking issue${issues.length === 1 ? '' : 's'}. Review them before trying again.`,
                    'warning'
                );
            } else {
                review.approvalError = error.message || 'The approval transaction failed.';
                showInlineStatus(`Script could not be approved. ${review.approvalError}`, 'error');
            }
        } finally {
            review.approving = false;
            renderScriptReviewMetadata();
            renderScriptEntryList();
        }
    }

    function openLegacyScriptTool(action) {
        document.body.classList.add('script-legacy-mode');
        requestAnimationFrame(() => {
            let target = null;
            if (action === 'generate') target = element('script-generation-workflow');
            if (action === 'chatgpt') {
                target = element('script-external-workflow');
                if (target) target.open = true;
            }
            if (action === 'import') {
                target = element('script-import-workflow');
                if (target) target.open = true;
            }
            if (action === 'versions') {
                target = Array.from(document.querySelectorAll('#script-tab > details')).find(details =>
                    details.querySelector('summary')?.textContent.trim() === 'Saved script versions'
                );
                if (target) target.open = true;
            }
            (target || element('script-generation-workflow'))?.scrollIntoView({ block: 'start' });
        });
    }

    function setupScriptReview() {
        element('script-review-search')?.addEventListener('input', event => {
            state.scriptReview.query = event.target.value;
            state.scriptReview.visibleLimit = 80;
            renderScriptEntryList();
        });
        element('script-review-filters')?.addEventListener('click', event => {
            const button = event.target.closest('[data-script-filter]');
            if (!button || button.disabled) return;
            state.scriptReview.filter = button.dataset.scriptFilter;
            state.scriptReview.visibleLimit = 80;
            const issues = filteredScriptIssues();
            if (issues.length && !issues.some(issue => issue.id === state.scriptReview.selectedIssueId)) {
                state.scriptReview.selectedIssueId = issues[0].id;
                if (issues[0].entryIndex !== null) state.scriptReview.selectedIndex = issues[0].entryIndex;
            }
            renderScriptEntryList();
            if (issues.length && window.innerWidth < 1180) renderScriptInspector({ open: true });
        });
        element('script-entry-list')?.addEventListener('click', event => {
            if (event.target.closest('#script-clear-filters')) {
                state.scriptReview.filter = 'all';
                state.scriptReview.query = '';
                const search = element('script-review-search');
                if (search) search.value = '';
                renderScriptEntryList();
                return;
            }
            const row = event.target.closest('[data-script-entry-index]');
            if (!row) return;
            state.scriptReview.selectedIndex = Number(row.dataset.scriptEntryIndex);
            state.scriptReview.selectedIssueId = row.dataset.scriptIssueId || '__none__';
            renderScriptEntryList();
            renderScriptInspector({ open: true });
            row.scrollIntoView({ block: 'nearest' });
        });
        element('script-entry-previous')?.addEventListener('click', () => {
            if (normalizedScriptIssues().length) {
                moveScriptIssue(-1);
                return;
            }
            state.scriptReview.selectedIndex = Math.max(0, state.scriptReview.selectedIndex - 1);
            renderScriptEntryList();
            document.querySelector(`[data-script-entry-index="${state.scriptReview.selectedIndex}"]`)?.scrollIntoView({ block: 'center' });
        });
        element('script-entry-next')?.addEventListener('click', () => {
            if (normalizedScriptIssues().length) {
                moveScriptIssue(1);
                return;
            }
            state.scriptReview.selectedIndex = Math.min(state.scriptReview.entries.length - 1, state.scriptReview.selectedIndex + 1);
            if (state.scriptReview.selectedIndex >= state.scriptReview.visibleLimit) state.scriptReview.visibleLimit += 80;
            renderScriptEntryList();
            document.querySelector(`[data-script-entry-index="${state.scriptReview.selectedIndex}"]`)?.scrollIntoView({ block: 'center' });
        });
        element('script-issue-previous')?.addEventListener('click', () => moveScriptIssue(-1));
        element('script-issue-next')?.addEventListener('click', () => moveScriptIssue(1));
        element('script-inspector-close')?.addEventListener('click', () => {
            element('script-review-inspector')?.classList.remove('is-open');
        });
        element('script-load-more')?.addEventListener('click', () => {
            state.scriptReview.visibleLimit += 80;
            renderScriptEntryList();
        });
        element('script-review-loading')?.addEventListener('click', event => {
            if (event.target.closest('#script-review-retry')) loadScriptReview({ force: true });
        });
        document.querySelectorAll('[data-script-legacy-action]').forEach(button => {
            button.addEventListener('click', () => openLegacyScriptTool(button.dataset.scriptLegacyAction));
        });
        element('script-return-review')?.addEventListener('click', () => {
            document.body.classList.remove('script-legacy-mode');
            element('script-review-workspace')?.scrollIntoView({ block: 'start' });
            loadScriptReview({ force: true });
        });
    }

    function castInitials(character) {
        const name = text(character?.display_name || character?.canonical_name, 'Character');
        const parts = name.split(/\s+/).filter(Boolean);
        return parts.slice(0, 2).map(part => part.charAt(0)).join('').toUpperCase() || 'A';
    }

    function castStatePresentation(character) {
        const readiness = character?.readiness_state;
        if (readiness === 'ready') return { label: 'Voice assigned', state: 'ready' };
        if (readiness === 'needs_voice') return { label: 'Missing voice', state: 'warning' };
        if (readiness === 'needs_identity_review') return { label: 'Identity review', state: 'error' };
        if (character?.speaking_role !== 'speaking') return { label: 'No voice needed', state: 'neutral' };
        return { label: titleCase(readiness || 'Needs attention'), state: 'warning' };
    }

    function applyCastRouteContext(route) {
        const context = route?.context || {};
        const allowedFilters = new Set([
            'needs_attention',
            'unassigned',
            'speaking_roles',
            'ready',
        ]);
        state.cast.search = String(context.search || '');
        state.cast.filter = allowedFilters.has(context.filter)
            ? context.filter
            : 'speaking_roles';
        if (context.character) state.cast.selectedId = context.character;

        const search = element('cast-search');
        if (search && search.value !== state.cast.search) {
            search.value = state.cast.search;
        }
        document.querySelectorAll('[data-cast-filter]').forEach(button => {
            const active = button.dataset.castFilter === state.cast.filter;
            button.classList.toggle('is-active', active);
            button.setAttribute('aria-pressed', String(active));
        });
    }

    function filteredCastCharacters() {
        const cast = state.cast;
        const query = cast.search.trim().toLocaleLowerCase();
        return (cast.aggregate?.characters || []).filter(character => {
            const matchesSearch = !query || [
                character.display_name,
                character.canonical_name,
                character.voice_summary,
                character.script_connection?.resolved_script_voice_label,
                ...(character.identity?.aliases || []),
            ].some(value => String(value || '').toLocaleLowerCase().includes(query));
            if (!matchesSearch) return false;
            if (cast.filter === 'needs_attention') return Number(character.blocker_count || 0) > 0;
            if (cast.filter === 'unassigned') return character.readiness_state === 'needs_voice';
            if (cast.filter === 'speaking_roles') return character.speaking_role === 'speaking';
            if (cast.filter === 'ready') return character.readiness_state === 'ready';
            return true;
        });
    }

    function renderCastOverview() {
        const aggregate = state.cast.aggregate || {};
        const summary = aggregate.summary || {};
        const title = element('cast-overview-title');
        const copy = element('cast-overview-copy');
        const counts = element('cast-overview-counts');
        if (title) {
            title.textContent = summary.complete
                ? 'Every required character has a valid production Voice'
                : `${Number(summary.blocker_count || 0).toLocaleString()} Cast ${Number(summary.blocker_count || 0) === 1 ? 'blocker remains' : 'blockers remain'}`;
        }
        if (copy) {
            copy.textContent = summary.complete
                ? 'Cast is ready to continue to Produce.'
                : 'Resolve missing Voices, identity reviews, and invalid clone references before production.';
        }
        if (counts) {
            counts.innerHTML = `
                <span><strong>${Number(summary.character_count || 0).toLocaleString()}</strong> characters</span>
                <span><strong>${Number(summary.ready_required_count || 0).toLocaleString()}</strong> ready</span>
                <span><strong>${Number(summary.blocker_count || 0).toLocaleString()}</strong> blockers</span>
            `;
        }
        const filterCounts = aggregate.filters?.counts || {};
        const values = {
            'cast-filter-attention': filterCounts.needs_attention,
            'cast-filter-unassigned': filterCounts.unassigned,
            'cast-filter-speaking': filterCounts.speaking_roles,
            'cast-filter-ready': filterCounts.ready,
        };
        Object.entries(values).forEach(([id, value]) => {
            const target = element(id);
            if (target) target.textContent = Number(value || 0).toLocaleString();
        });
    }

    function renderCastList() {
        const list = element('cast-character-list');
        if (!list) return;
        const characters = filteredCastCharacters();
        if (characters.length && !characters.some(character => character.character_id === state.cast.selectedId)) {
            state.cast.selectedId = characters[0].character_id;
        }
        list.innerHTML = characters.length
            ? characters.map(character => {
                const presentation = castStatePresentation(character);
                const label = character.script_connection?.resolved_script_voice_label;
                return `
                    <button type="button" class="cast-character-row" role="option" aria-selected="${character.character_id === state.cast.selectedId}" data-cast-character-id="${escapeHtml(character.character_id)}">
                        <span class="cast-character-portrait" aria-hidden="true">${escapeHtml(castInitials(character))}</span>
                        <span class="cast-character-copy">
                            <span class="cast-character-name">${escapeHtml(text(character.display_name || character.canonical_name, 'Character'))}</span>
                            <span class="cast-character-meta">
                                <span class="cast-character-status" data-state="${presentation.state}">${escapeHtml(presentation.label)}</span>
                                <span>${escapeHtml(text(label, 'No Script label'))}</span>
                            </span>
                        </span>
                    </button>
                `;
            }).join('')
            : `
                <div class="canonical-empty-state">
                    <div><strong>No characters match</strong><p>Clear the search or choose another filter.</p></div>
                </div>
            `;
        renderCastDetail();
    }

    function castSummaryDefinition(character) {
        const summary = character.character?.summary || character.identity || {};
        const aliases = summary.aliases || character.identity?.aliases || [];
        const relationships = summary.relationships || character.identity?.relationships || [];
        return `
            <dl>
                <div><dt>Canonical name</dt><dd>${escapeHtml(text(summary.canonical_name || character.canonical_name))}</dd></div>
                <div><dt>Aliases</dt><dd>${escapeHtml(aliases.length ? aliases.join(', ') : 'None recorded')}</dd></div>
                <div><dt>Type</dt><dd>${escapeHtml(text(summary.species_or_type, 'Not specified'))}</dd></div>
                <div><dt>Relationships</dt><dd>${escapeHtml(relationships.length ? relationships.map(item => typeof item === 'string' ? item : item.label || item.name).filter(Boolean).join(', ') : 'None recorded')}</dd></div>
            </dl>
        `;
    }

    function appearanceSummary(character) {
        const appearance = character.appearance || {};
        if (!appearance.evidence_available) {
            return '<p>No evidence-backed appearance dossier is required for this Voice assignment.</p>';
        }
        const traits = appearance.stable_traits || [];
        return `
            <p>${escapeHtml(text(appearance.summary, 'Evidence-backed appearance is available.'))}</p>
            ${traits.length ? `<ul>${traits.map(trait => `<li>${escapeHtml(typeof trait === 'string' ? trait : trait.label || trait.value || JSON.stringify(trait))}</li>`).join('')}</ul>` : ''}
        `;
    }

    function advancedCastSummary(character) {
        const connection = character.script_connection || {};
        const identity = character.identity || {};
        const blockers = character.blockers || [];
        return `
            <dl>
                <div><dt>Script label</dt><dd>${escapeHtml(text(connection.resolved_script_voice_label, 'Unresolved'))}</dd></div>
                <div><dt>Mapping</dt><dd>${escapeHtml(titleCase(connection.mapping_method || 'unknown'))}</dd></div>
                <div><dt>Script lines</dt><dd>${Number(connection.script_line_count || 0).toLocaleString()}</dd></div>
                <div><dt>Source confidence</dt><dd>${identity.source_confidence == null ? 'Not recorded' : `${Math.round(Number(identity.source_confidence) * 100)}%`}</dd></div>
                <div><dt>Current blockers</dt><dd>${escapeHtml(blockers.length ? blockers.map(item => item.title).join('; ') : 'None')}</dd></div>
            </dl>
        `;
    }

    function selectedCastCharacter() {
        const aggregate = state.cast.aggregate || {};
        return (aggregate.characters || []).find(item => item.character_id === state.cast.selectedId)
            || aggregate.selected_character
            || null;
    }

    function renderCastVoiceEditorState() {
        const cast = state.cast;
        const character = selectedCastCharacter();
        const editor = element('cast-voice-editor');
        const edit = element('cast-edit-voice');
        const save = element('cast-save-voice');
        const cancel = element('cast-cancel-voice');
        const savedState = element('cast-voice-saved-state');
        if (editor) editor.hidden = !cast.editing;
        if (edit) {
            edit.hidden = cast.editing;
            edit.disabled = cast.saving || !(
                character?.voice?.configuration_key
                || character?.script_connection?.resolved_script_voice_label
            );
        }
        if (save) {
            save.hidden = !cast.editing || !cast.dirty;
            save.disabled = cast.saving;
            save.textContent = cast.saving ? 'Saving…' : 'Save changes';
        }
        if (cancel) cancel.disabled = cast.saving;
        if (savedState) {
            if (cast.saving) {
                savedState.textContent = 'Saving…';
                savedState.dataset.state = 'running';
            } else if (cast.dirty) {
                savedState.textContent = 'Unsaved changes';
                savedState.dataset.state = 'warning';
            } else {
                savedState.textContent = character?.voice?.valid ? 'Saved' : 'Needs attention';
                savedState.dataset.state = character?.voice?.valid ? 'saved' : 'warning';
            }
        }
        document.body.classList.toggle('cast-voice-editing', cast.editing);
    }

    function markCastVoiceDirty() {
        if (!state.cast.editing || state.cast.saving) return;
        state.cast.dirty = true;
        renderCastVoiceEditorState();
    }

    async function beginCastVoiceEdit() {
        if (state.cast.editing || state.cast.saving) return;
        const character = selectedCastCharacter();
        const voiceName = character?.voice?.configuration_key
            || character?.script_connection?.resolved_script_voice_label;
        if (!voiceName) {
            showInlineStatus('This character has no resolved Script Voice label to edit.', 'warning');
            return;
        }
        state.cast.editing = true;
        state.cast.dirty = false;
        state.cast.editVoiceName = voiceName;
        const slot = element('cast-voice-editor-slot');
        if (slot) {
            slot.innerHTML = '<div class="cast-editor-loading"><span class="spinner-border spinner-border-sm" aria-hidden="true"></span><span>Loading Voice controls…</span></div>';
        }
        renderCastVoiceEditorState();
        try {
            const mounted = await window.AlexandriaVoiceCardBridge?.mountCast?.(voiceName);
            if (!mounted) throw new Error(`Voice controls for ${voiceName} are unavailable.`);
            element('cast-voice-editor')?.scrollIntoView({ block: 'nearest' });
            element('cast-voice-editor-slot')?.querySelector('input, select, textarea, button')?.focus({ preventScroll: true });
        } catch (error) {
            state.cast.editing = false;
            state.cast.editVoiceName = null;
            window.AlexandriaVoiceCardBridge?.releaseCast?.();
            renderCastVoiceEditorState();
            showInlineStatus(`Voice configuration could not be opened. ${error.message}`, 'error');
        }
    }

    async function closeCastVoiceEdit({ restore = false } = {}) {
        if (state.cast.saving) return false;
        const reset = restore || state.cast.dirty;
        state.cast.editing = false;
        state.cast.dirty = false;
        state.cast.editVoiceName = null;
        renderCastVoiceEditorState();
        if (reset) await window.AlexandriaVoiceCardBridge?.resetCast?.();
        else window.AlexandriaVoiceCardBridge?.releaseCast?.();
        return true;
    }

    async function saveCastVoiceEdit() {
        if (!state.cast.editing || state.cast.saving) return;
        if (!state.cast.dirty) {
            await closeCastVoiceEdit();
            return;
        }
        state.cast.saving = true;
        renderCastVoiceEditorState();
        try {
            const bridge = window.AlexandriaVoiceCardBridge;
            if (!bridge?.saveCast) throw new Error('The Voice editor bridge is unavailable.');
            await bridge.saveCast();
            state.cast.saving = false;
            state.cast.editing = false;
            state.cast.dirty = false;
            state.cast.editVoiceName = null;
            await window.AlexandriaVoiceCardBridge?.resetCast?.();
            await loadCast({ force: true });
            showInlineStatus('Voice configuration saved.', 'success');
        } catch (error) {
            state.cast.saving = false;
            renderCastVoiceEditorState();
            showInlineStatus(`Voice configuration could not be saved. ${error.message}`, 'error');
        }
    }

    window.AlexandriaCastVoiceEditor = Object.freeze({
        markDirty: markCastVoiceDirty,
        isActive: () => state.cast.editing,
    });

    function renderCastDetail() {
        const character = selectedCastCharacter();
        const empty = element('cast-detail-empty');
        const content = element('cast-detail-content');
        if (!character) {
            if (empty) empty.hidden = false;
            if (content) content.hidden = true;
            return;
        }
        if (empty) empty.hidden = true;
        if (content) content.hidden = false;
        const presentation = castStatePresentation(character);
        const identity = character.identity || {};
        const connection = character.script_connection || {};
        const voice = character.voice || {};
        const clone = voice.clone || {};
        const preview = voice.preview || {};
        const set = (id, value) => {
            const target = element(id);
            if (target) target.textContent = value;
        };
        set('cast-detail-portrait', castInitials(character));
        set('cast-detail-name', text(character.display_name || character.canonical_name, 'Character'));
        set('cast-detail-script-label', text(connection.resolved_script_voice_label, 'Script label unresolved'));
        set('cast-detail-role', character.speaking_role === 'speaking' ? 'Speaking role' : 'Non-speaking');
        const detailState = element('cast-detail-state');
        if (detailState) {
            detailState.textContent = presentation.label;
            detailState.dataset.state = presentation.state;
        }
        const blockers = character.blockers || [];
        const blockerSummary = element('cast-blocker-summary');
        if (blockerSummary) {
            blockerSummary.hidden = blockers.length === 0;
            blockerSummary.textContent = blockers.length
                ? `${blockers[0].title}. ${blockers[0].explanation}${blockers.length > 1 ? ` ${blockers.length - 1} more ${blockers.length - 1 === 1 ? 'blocker' : 'blockers'} remain.` : ''}`
                : '';
        }
        set('cast-voice-method', titleCase(voice.selected_production_method || 'not assigned'));
        set('cast-assigned-voice', text(voice.selected_voice || voice.alias?.target, voice.valid ? 'Configured Voice' : 'Missing voice'));
        set('cast-voice-description', text(voice.persistent_voice_description, 'Not recorded'));
        set('cast-delivery-control', clone.controlled_capability ? 'Instruction-controlled clone — experimental' : 'Standard per-line directions');
        const controlledWarning = element('cast-controlled-warning');
        if (controlledWarning) controlledWarning.hidden = !clone.controlled_capability;
        const previewLabel = preview.approved ? 'Approved preview' : preview.listened ? 'Listened — approval required' : titleCase(preview.status || 'not generated');
        set('cast-preview-state', previewLabel);
        set('cast-preview-copy', preview.approved
            ? 'The current Voice configuration has an approved listening check.'
            : preview.listened
                ? 'The preview was heard but still requires approval before production.'
                : 'Edit this Voice to generate or review its preview.');
        set('cast-reference-state', clone.reference_audio_state === 'ready' ? 'Reference audio ready' : titleCase(clone.reference_audio_state || 'No reference required'));
        set('cast-reference-file', text(clone.reference_source, clone.reference_audio_state === 'ready' ? 'Saved supplied recording' : voice.selected_production_method === 'clone' ? 'Reference source not recorded' : 'Not required'));
        set('cast-reference-transcript', text(clone.exact_reference_transcript, voice.selected_production_method === 'clone' ? 'Exact transcript is required for this clone.' : 'No exact transcript required.'));
        const editButton = element('cast-edit-voice');
        if (editButton) editButton.disabled = !voice.configuration_key && !connection.resolved_script_voice_label;
        set('cast-character-summary-label', `${Number(connection.script_line_count || 0).toLocaleString()} Script lines`);
        const characterSummary = element('cast-character-summary');
        if (characterSummary) characterSummary.innerHTML = castSummaryDefinition(character);
        set('cast-appearance-summary-label', character.appearance?.evidence_available ? titleCase(character.appearance.status || 'Available') : 'No evidence required');
        const appearance = element('cast-appearance-summary');
        if (appearance) appearance.innerHTML = appearanceSummary(character);
        const advanced = element('cast-advanced-summary');
        if (advanced) advanced.innerHTML = advancedCastSummary(character);
        renderCastVoiceEditorState();
    }

    async function loadCast({ force = false } = {}) {
        const cast = state.cast;
        const request = ++cast.request;
        const loading = element('cast-loading');
        const content = element('cast-content');
        if (!force && cast.aggregate) {
            renderCastOverview();
            renderCastList();
            return;
        }
        if (loading) loading.hidden = false;
        if (content) content.hidden = true;
        try {
            const query = new URLSearchParams();
            const selected = state.route.context.character || cast.selectedId;
            if (selected) query.set('selected_character_id', selected);
            const aggregate = await fetchJson(`/api/cast${query.toString() ? `?${query}` : ''}`);
            if (request !== cast.request) return;
            cast.aggregate = aggregate;
            cast.selectedId = selected || aggregate.selected_character_id || aggregate.characters?.[0]?.character_id || null;
            renderCastOverview();
            renderCastList();
            if (loading) loading.hidden = true;
            if (content) content.hidden = false;
        } catch (error) {
            if (request !== cast.request) return;
            if (loading) {
                loading.hidden = false;
                loading.innerHTML = `
                    <div class="canonical-error-state" role="alert">
                        <div><strong>Cast could not be loaded</strong><p>${escapeHtml(error.message)}</p></div>
                        <button type="button" class="btn btn-outline-secondary" id="cast-retry">Retry</button>
                    </div>
                `;
            }
        }
    }

    function setupCast() {
        element('cast-search')?.addEventListener('input', event => {
            state.cast.search = event.target.value;
            renderCastList();
        });
        element('cast-filter-grid')?.addEventListener('click', event => {
            const button = event.target.closest('[data-cast-filter]');
            if (!button) return;
            state.cast.filter = button.dataset.castFilter;
            document.querySelectorAll('[data-cast-filter]').forEach(item => {
                const active = item === button;
                item.classList.toggle('is-active', active);
                item.setAttribute('aria-pressed', String(active));
            });
            window.AlexandriaNavigation?.updateContext(
                { filter: state.cast.filter },
                { historyMode: 'replace' }
            );
            renderCastList();
        });
        element('cast-character-list')?.addEventListener('click', async event => {
            const row = event.target.closest('[data-cast-character-id]');
            if (!row || row.dataset.castCharacterId === state.cast.selectedId) return;
            if (state.cast.editing && state.cast.dirty) {
                const confirmed = typeof window.showConfirm === 'function'
                    ? await window.showConfirm('Discard unsaved Voice changes and select another character?')
                    : window.confirm('Discard unsaved Voice changes and select another character?');
                if (!confirmed) return;
            }
            if (state.cast.editing) await closeCastVoiceEdit({ restore: state.cast.dirty });
            state.cast.selectedId = row.dataset.castCharacterId;
            renderCastList();
            window.AlexandriaNavigation?.updateContext(
                { character: state.cast.selectedId },
                { historyMode: 'replace' }
            );
        });
        element('cast-edit-voice')?.addEventListener('click', beginCastVoiceEdit);
        element('cast-save-voice')?.addEventListener('click', saveCastVoiceEdit);
        element('cast-cancel-voice')?.addEventListener('click', async () => {
            if (state.cast.dirty) {
                const confirmed = typeof window.showConfirm === 'function'
                    ? await window.showConfirm('Discard unsaved Voice changes?')
                    : window.confirm('Discard unsaved Voice changes?');
                if (!confirmed) return;
            }
            await closeCastVoiceEdit({ restore: state.cast.dirty });
        });
        element('cast-loading')?.addEventListener('click', event => {
            if (event.target.closest('#cast-retry')) loadCast({ force: true });
        });
    }

    function produceStateLabel(value) {
        return {
            ready: 'Ready to generate',
            generating: 'Generating',
            needs_listening: 'Needs listening',
            needs_review: 'Needs review',
            current: 'Current',
            stale: 'Stale',
            failed: 'Failed',
            missing_voice: 'Blocked',
        }[value] || titleCase(value || 'unknown');
    }

    function formatDurationMilliseconds(value) {
        const milliseconds = Number(value);
        if (!Number.isFinite(milliseconds) || milliseconds <= 0) return '—';
        const totalSeconds = Math.round(milliseconds / 1000);
        const minutes = Math.floor(totalSeconds / 60);
        return `${minutes}:${String(totalSeconds % 60).padStart(2, '0')}`;
    }

    function waveformBars() {
        return Array.from({ length: 9 }, () => '<span></span>').join('');
    }

    function produceCharacterMonogram(chunk) {
        const name = text(chunk?.character_name || chunk?.speaker, 'Narrator');
        return name.split(/\s+/).filter(Boolean).slice(0, 2).map(part => part.charAt(0)).join('').toUpperCase() || 'N';
    }

    function produceReasonText(chunk) {
        const blocker = chunk?.blockers?.[0];
        if (blocker) return `${blocker.title}: ${blocker.explanation}`;
        return {
            audio_invalidated: 'Script, Voice, or generation settings changed after this audio was created.',
            audio_not_current: 'This file is no longer eligible as current production audio.',
            audio_fingerprint_mismatch: 'Script text, delivery direction, Voice, or generation settings changed after this audio was created.',
            audio_not_generated: 'This chunk has not been generated yet.',
            generation_running: 'Generation is currently running for this chunk.',
            generation_failed: 'The latest generation attempt failed.',
            listening_required: 'A person must listen to and approve this audio before Export.',
            operator_review_required: 'This audio requires production review before Export.',
        }[chunk?.reason] || titleCase(chunk?.reason || 'Current dependency state');
    }

    function groupProduceChunks(chunks) {
        const groups = [];
        let current = null;
        chunks.forEach(chunk => {
            const explicit = text(
                chunk.chapter_name || chunk.chapter || chunk.scene_name || chunk.scene,
                ''
            );
            const heading = /^(chapter|part|book|volume|prologue|epilogue|introduction|act|section)\b/i.test(String(chunk.text || '').trim())
                ? String(chunk.text || '').trim().slice(0, 120)
                : '';
            const label = explicit || heading;
            if (!current || (label && label !== current.label)) {
                current = {
                    label: label || (groups.length ? `Continuation ${groups.length + 1}` : 'Entire Script'),
                    chunks: [],
                };
                groups.push(current);
            }
            current.chunks.push(chunk);
        });
        return groups;
    }

    function renderProduceCounts() {
        const aggregate = state.produce.aggregate || {};
        const counts = aggregate.counts || {};
        const values = {
            'produce-count-all': aggregate.all_chunk_count,
            'produce-count-ready': counts.ready,
            'produce-count-listening': counts.needs_listening,
            'produce-count-failed': counts.failed,
            'produce-count-stale': counts.stale,
            'produce-count-current': counts.current,
            'produce-count-blocked': counts.missing_voice,
        };
        Object.entries(values).forEach(([id, value]) => {
            const target = element(id);
            if (target) target.textContent = Number(value || 0).toLocaleString();
        });
        const summary = element('produce-visible-summary');
        if (summary) {
            const filtered = aggregate.page?.filtered_chunk_count ?? aggregate.visible_chunk_count ?? state.produce.chunks.length;
            summary.textContent = `${Number(filtered || 0).toLocaleString()} ${Number(filtered || 0) === 1 ? 'chunk' : 'chunks'}`;
        }
        const retry = element('produce-retry-failed');
        if (retry) retry.disabled = !Number(counts.failed || 0);
    }

    function renderProduceProgress() {
        const process = state.produce.aggregate?.process || {};
        const banner = element('produce-progress-banner');
        if (!banner) return;
        banner.hidden = !process.running;
        if (!process.running) return;
        const total = Number(process.total_count || 0);
        const completed = Number(process.completed_count || 0);
        const failed = Number(process.failed_count || 0);
        const progress = total > 0 ? Math.min(100, Math.round(((completed + failed) / total) * 100)) : 0;
        const title = element('produce-progress-title');
        const copy = element('produce-progress-copy');
        const bar = element('produce-progress-bar');
        if (title) title.textContent = process.cancel_requested ? 'Cancelling audio generation…' : 'Generating audio';
        if (copy) copy.textContent = `${completed.toLocaleString()} of ${total.toLocaleString()} chunks · ${failed.toLocaleString()} failed`;
        if (bar) bar.style.width = `${progress}%`;
        const progressRoot = banner.querySelector('[role="progressbar"]');
        progressRoot?.setAttribute('aria-valuenow', String(progress));
        const cancel = element('produce-cancel-generation');
        if (cancel) cancel.disabled = Boolean(process.cancel_requested);
    }

    function renderProduceList({ append = false } = {}) {
        const list = element('produce-chunk-list');
        if (!list) return;
        const chunks = state.produce.chunks;
        const markup = chunks.length
            ? groupProduceChunks(chunks).map(group => `
                <section class="produce-chapter-group" role="group" aria-label="${escapeHtml(group.label)}">
                    <div class="produce-chapter-heading"><span class="canonical-kicker">Chapter or scene</span><strong>${escapeHtml(group.label)}</strong></div>
                    ${group.chunks.map(chunk => `
                        <div class="produce-chunk-row" role="option" tabindex="0" aria-selected="${chunk.chunk_id === state.produce.selectedId}" data-produce-chunk-id="${escapeHtml(chunk.chunk_id)}" data-audio-state="${escapeHtml(chunk.state)}" aria-label="${escapeHtml(text(chunk.character_name || chunk.speaker, 'Narrator'))}, ${escapeHtml(produceStateLabel(chunk.state))}. ${escapeHtml(produceReasonText(chunk))}">
                            <span class="produce-row-character">
                                <span class="produce-row-avatar" aria-hidden="true">${escapeHtml(produceCharacterMonogram(chunk))}</span>
                                <span>${escapeHtml(text(chunk.character_name || chunk.speaker, 'Narrator'))}</span>
                            </span>
                            <span class="produce-row-copy"><span class="produce-row-text">${escapeHtml(text(chunk.text_excerpt || chunk.text, 'No text'))}</span></span>
                            <span class="produce-row-direction"><span class="visually-hidden">Delivery direction: </span>${escapeHtml(text(chunk.delivery_direction, 'No delivery direction recorded.'))}</span>
                            <span class="produce-row-duration">${escapeHtml(formatDurationMilliseconds(chunk.duration_ms))}</span>
                            <span class="produce-row-audio">
                                <button type="button" class="produce-row-play" data-produce-play="${escapeHtml(chunk.chunk_id)}" aria-label="Play ${escapeHtml(text(chunk.character_name || chunk.speaker, 'chunk'))}" title="Play chunk" ${chunk.audio?.available ? '' : 'disabled'}><i class="fas fa-play" aria-hidden="true"></i></button>
                                <span class="produce-row-wave" aria-hidden="true">${waveformBars()}</span>
                            </span>
                            <span class="produce-row-state" data-state="${escapeHtml(chunk.state)}">${escapeHtml(produceStateLabel(chunk.state))}</span>
                            <button type="button" class="produce-row-menu" data-produce-regenerate="${escapeHtml(chunk.chunk_id)}" aria-label="${chunk.regenerate_action ? escapeHtml(chunk.regenerate_action.label) : 'Regeneration unavailable'}" title="${chunk.regenerate_action ? escapeHtml(chunk.regenerate_action.label) : 'Regeneration unavailable'}" ${chunk.regenerate_action ? '' : 'disabled'}><i class="fas fa-rotate-right" aria-hidden="true"></i></button>
                        </div>
                    `).join('')}
                </section>
            `).join('')
            : `
                <div class="canonical-empty-state">
                    <div><strong>No audio chunks match</strong><p>Choose another filter or clear the search.</p></div>
                </div>
            `;
        if (append) list.insertAdjacentHTML('beforeend', markup);
        else list.innerHTML = markup;
        const count = element('produce-list-count');
        const page = state.produce.aggregate?.page || {};
        if (count) count.textContent = `${state.produce.chunks.length.toLocaleString()} shown of ${Number(page.filtered_chunk_count ?? state.produce.aggregate?.visible_chunk_count ?? state.produce.chunks.length).toLocaleString()}`;
        const more = element('produce-load-more');
        if (more) more.hidden = !page.has_more;
        renderProduceInspector();
    }

    function selectedProduceChunk() {
        return state.produce.selectedChunk
            || state.produce.chunks.find(chunk => chunk.chunk_id === state.produce.selectedId)
            || null;
    }

    function renderProduceInspector({ open = false } = {}) {
        const chunk = selectedProduceChunk();
        const inspector = element('produce-inspector');
        const set = (id, value) => {
            const target = element(id);
            if (target) target.textContent = value;
        };
        if (!chunk) {
            set('produce-inspector-speaker', 'Select a chunk');
            set('produce-inspector-index', '—');
            set('produce-inspector-text', 'Choose a chunk to inspect its production details.');
            set('produce-inspector-direction', '—');
            element('produce-play-selected').disabled = true;
            element('produce-regenerate-selected').disabled = true;
            return;
        }
        set('produce-inspector-speaker', text(chunk.character_name || chunk.speaker, 'Narrator'));
        set('produce-inspector-index', `Chunk ${Number(chunk.index || 0) + 1} of ${Number(state.produce.aggregate?.all_chunk_count || state.produce.chunks.length).toLocaleString()}`);
        set('produce-inspector-text', text(chunk.text, 'No text'));
        set('produce-inspector-direction', text(chunk.delivery_direction, 'No delivery direction recorded.'));
        set('produce-inspector-pause', chunk.pause_after_ms ? `${Number(chunk.pause_after_ms)} ms` : 'None');
        set('produce-inspector-voice', text(chunk.voice?.configuration_key || chunk.voice?.resolved_speaker, chunk.voice?.valid ? 'Configured Voice' : 'Missing voice'));
        set('produce-inspector-state', produceStateLabel(chunk.state));
        set('produce-inspector-reason', produceReasonText(chunk));
        const play = element('produce-play-selected');
        if (play) play.disabled = !chunk.audio?.available;
        const regenerate = element('produce-regenerate-selected');
        if (regenerate) regenerate.disabled = !chunk.regenerate_action || chunk.state === 'generating' || chunk.state === 'missing_voice';
        const waveform = element('produce-inspector-waveform');
        if (waveform) {
            waveform.style.opacity = chunk.audio?.available ? '1' : '0.35';
            waveform.setAttribute('aria-label', chunk.audio?.available ? `Audio preview for ${text(chunk.character_name || chunk.speaker, 'selected chunk')}` : 'Audio preview unavailable');
        }
        const history = element('produce-history-copy');
        if (history) {
            const technical = chunk.technical_details || {};
            history.textContent = technical.recorded_audio_fingerprint
                ? `Recorded audio fingerprint ${String(technical.recorded_audio_fingerprint).slice(0, 12)}… · verification ${titleCase(chunk.audio?.verification_level || 'unknown')}.`
                : 'No successful generation record is available for this chunk.';
        }
        if (open && window.innerWidth < 1180) inspector?.classList.add('is-open');
    }

    function setPersistentAudioFromChunk(chunk, { autoplay = true } = {}) {
        if (!chunk?.audio?.available || !chunk.audio.url) return;
        const audio = element('main-audio');
        const source = audio?.querySelector('source');
        if (!audio || !source) return;
        source.src = chunk.audio.url;
        source.type = 'audio/mpeg';
        const title = element('persistent-player-title');
        const context = element('persistent-player-context');
        if (title) title.textContent = text(chunk.character_name || chunk.speaker, 'Audio chunk');
        if (context) context.textContent = text(chunk.text_excerpt || chunk.text, 'Production audio');
        audio.load();
        if (autoplay) audio.play().catch(() => {});
    }

    async function loadProduce({ append = false, force = false } = {}) {
        const produce = state.produce;
        const request = ++produce.request;
        const loading = element('produce-loading');
        const content = element('produce-content');
        if (force) {
            produce.offset = 0;
            produce.chunks = [];
            append = false;
        }
        if (!append && loading) loading.hidden = false;
        if (!append && content && !produce.aggregate) content.hidden = true;
        const query = new URLSearchParams({
            filter: produce.filter,
            offset: String(produce.offset),
            limit: String(produce.limit),
        });
        if (produce.search.trim()) query.set('search', produce.search.trim());
        const selected = state.route.context.chunk || produce.selectedId;
        if (selected) query.set('selected_chunk_id', selected);
        try {
            const aggregate = await fetchJson(`/api/produce?${query}`);
            if (request !== produce.request) return;
            produce.aggregate = aggregate;
            if (append) produce.chunks = produce.chunks.concat(aggregate.chunks || []);
            else produce.chunks = aggregate.chunks || [];
            const actionableSelection = produce.chunks.find(chunk => chunk.state === 'stale')
                || produce.chunks.find(chunk => ['failed', 'needs_listening', 'needs_review', 'ready'].includes(chunk.state))
                || produce.chunks[0]
                || null;
            produce.selectedId = selected || aggregate.selected_chunk_id || actionableSelection?.chunk_id || null;
            produce.selectedChunk = aggregate.selected_chunk
                || produce.chunks.find(chunk => chunk.chunk_id === produce.selectedId)
                || actionableSelection;
            renderProduceCounts();
            renderProduceProgress();
            renderProduceList();
            renderPrimaryAction(state.route, state.flow?.stage_map?.produce || null);
            const subtitle = element('shell-page-subtitle');
            if (subtitle) {
                const counts = aggregate.counts || {};
                subtitle.textContent = aggregate.state === 'complete'
                    ? 'All production audio is current and ready for Export.'
                    : `${Number(counts.current || 0).toLocaleString()} current · ${Number(counts.ready || 0).toLocaleString()} ready · ${Number(counts.stale || 0).toLocaleString()} stale · ${Number(counts.failed || 0).toLocaleString()} failed · ${Number(counts.needs_listening || 0).toLocaleString()} need listening.`;
            }
            if (loading) loading.hidden = true;
            if (content) content.hidden = false;
            if (aggregate.process?.running) startProducePolling();
            else stopProducePolling();
        } catch (error) {
            if (request !== produce.request) return;
            if (loading) {
                loading.hidden = false;
                loading.innerHTML = `
                    <div class="canonical-error-state" role="alert">
                        <div><strong>Production audio could not be loaded</strong><p>${escapeHtml(error.message)}</p></div>
                        <button type="button" class="btn btn-outline-secondary" id="produce-retry">Retry</button>
                    </div>
                `;
            }
        }
    }

    function startProducePolling() {
        if (state.produce.polling) return;
        state.produce.polling = window.setInterval(() => {
            if (state.route.destination !== 'produce') {
                stopProducePolling();
                return;
            }
            loadProduce({ force: true });
        }, 1500);
    }

    function stopProducePolling() {
        if (!state.produce.polling) return;
        window.clearInterval(state.produce.polling);
        state.produce.polling = null;
    }

    async function executeProduceMode(mode, selectedChunkIds = []) {
        try {
            let confirmed = false;
            if (mode === 'regenerate_all') {
                confirmed = await showConfirm('Regenerate all audio? Existing current audio will be replaced only after each new file validates.');
                if (!confirmed) return;
            }
            const plan = await fetchJson('/api/produce/plan', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ mode, selected_chunk_ids: selectedChunkIds }),
            });
            const endpoint = mode === 'retry_failed' ? '/api/produce/retry-failed' : '/api/produce/generate';
            await fetchJson(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    mode,
                    selected_chunk_ids: selectedChunkIds,
                    plan_fingerprint: plan.plan_fingerprint,
                    chunks_fingerprint: plan.chunks_fingerprint,
                    confirm_regenerate_all: confirmed,
                }),
            });
            showInlineStatus('Audio generation queued.', 'success');
            await loadProduce({ force: true });
        } catch (error) {
            showInlineStatus(`Audio generation could not start. ${error.message}`, 'error');
        }
    }

    function setupProduce() {
        let searchTimer = null;
        element('produce-search')?.addEventListener('input', event => {
            state.produce.search = event.target.value;
            window.clearTimeout(searchTimer);
            searchTimer = window.setTimeout(() => loadProduce({ force: true }), 250);
        });
        element('produce-filters')?.addEventListener('click', event => {
            const button = event.target.closest('[data-produce-filter]');
            if (!button) return;
            state.produce.filter = button.dataset.produceFilter;
            document.querySelectorAll('[data-produce-filter]').forEach(item => {
                const active = item === button;
                item.classList.toggle('is-active', active);
                item.setAttribute('aria-pressed', String(active));
            });
            loadProduce({ force: true });
        });
        element('produce-chunk-list')?.addEventListener('click', async event => {
            const play = event.target.closest('[data-produce-play]');
            if (play) {
                const chunk = state.produce.chunks.find(item => item.chunk_id === play.dataset.producePlay);
                setPersistentAudioFromChunk(chunk);
                return;
            }
            const regenerate = event.target.closest('[data-produce-regenerate]');
            if (regenerate) {
                await executeProduceMode('selected', [regenerate.dataset.produceRegenerate]);
                return;
            }
            const row = event.target.closest('[data-produce-chunk-id]');
            if (!row) return;
            state.produce.selectedId = row.dataset.produceChunkId;
            state.produce.selectedChunk = state.produce.chunks.find(item => item.chunk_id === state.produce.selectedId) || null;
            renderProduceList();
            renderProduceInspector({ open: true });
            window.AlexandriaNavigation?.updateContext({ chunk: state.produce.selectedId }, { historyMode: 'replace' });
        });
        element('produce-chunk-list')?.addEventListener('keydown', event => {
            if (!['Enter', ' '].includes(event.key)) return;
            const row = event.target.closest('[data-produce-chunk-id]');
            if (!row || event.target.closest('button')) return;
            event.preventDefault();
            row.click();
        });
        element('produce-load-more')?.addEventListener('click', () => {
            const next = state.produce.aggregate?.page?.next_offset;
            if (next == null) return;
            state.produce.offset = next;
            loadProduce({ append: true });
        });
        element('produce-inspector-close')?.addEventListener('click', () => element('produce-inspector')?.classList.remove('is-open'));
        element('produce-play-selected')?.addEventListener('click', () => setPersistentAudioFromChunk(selectedProduceChunk()));
        element('produce-regenerate-selected')?.addEventListener('click', () => {
            if (state.produce.selectedId) executeProduceMode('selected', [state.produce.selectedId]);
        });
        element('produce-retry-failed')?.addEventListener('click', () => executeProduceMode('retry_failed'));
        element('produce-regenerate-all')?.addEventListener('click', () => executeProduceMode('regenerate_all'));
        element('produce-cancel-generation')?.addEventListener('click', async () => {
            try {
                await fetchJson('/api/produce/cancel', { method: 'POST' });
                await loadProduce({ force: true });
            } catch (error) {
                showInlineStatus(`Generation could not be cancelled. ${error.message}`, 'error');
            }
        });
        element('produce-loading')?.addEventListener('click', event => {
            if (event.target.closest('#produce-retry')) loadProduce({ force: true });
        });
    }

    function exportMetadataFromControls() {
        return {
            title: text(element('export-title')?.value, ''),
            author: text(element('export-author')?.value, ''),
            narrator: text(element('export-narrator')?.value, ''),
            year: text(element('export-year')?.value, ''),
            description: text(element('export-description')?.value, ''),
        };
    }

    function exportFormatPresentation(format) {
        return {
            mp3: {
                label: 'MP3 audio file',
                filename: 'cloned_audiobook.mp3',
                behavior: 'One compatible audio master ending in .mp3.',
                mime: 'audio/mpeg',
            },
            m4b: {
                label: 'M4B audiobook',
                filename: 'audiobook.m4b',
                behavior: 'One chaptered audiobook ending in .m4b.',
                mime: 'audio/mp4',
            },
            audacity: {
                label: 'Audacity project package',
                filename: 'audacity_export.zip',
                behavior: 'A ZIP package containing the editable project and audio assets.',
                mime: 'application/zip',
            },
            chapter_separated: {
                label: 'Separate chapter files',
                filename: 'chapter-files/',
                behavior: 'A folder of individually named chapter audio files.',
                mime: 'audio/mpeg',
            },
        }[format] || {
            label: titleCase(format),
            filename: 'audiobook',
            behavior: 'Output naming is determined by the selected format.',
            mime: 'application/octet-stream',
        };
    }

    function selectedExportFormat() {
        return document.querySelector('input[name="export-format"]:checked')?.value
            || state.export.format
            || 'mp3';
    }

    function exportPlanPayload() {
        return {
            metadata: exportMetadataFromControls(),
            formats: [selectedExportFormat()],
            chapter_mode: 'smart',
        };
    }

    function formatLongDurationMilliseconds(value) {
        const milliseconds = Number(value);
        if (!Number.isFinite(milliseconds) || milliseconds <= 0) return '—';
        const totalSeconds = Math.round(milliseconds / 1000);
        const hours = Math.floor(totalSeconds / 3600);
        const minutes = Math.floor((totalSeconds % 3600) / 60);
        const seconds = totalSeconds % 60;
        return hours
            ? `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
            : `${minutes}:${String(seconds).padStart(2, '0')}`;
    }

    function initializeExportControls(aggregate) {
        if (state.export.metadataInitialized) return;
        const metadata = aggregate?.metadata || {};
        const source = state.flow?.source || {};
        const project = state.flow?.project || {};
        const values = {
            'export-title': metadata.title || source.title || project.name || '',
            'export-author': metadata.author || source.author || '',
            'export-narrator': metadata.narrator || '',
            'export-year': metadata.year || '',
            'export-description': metadata.description || '',
        };
        Object.entries(values).forEach(([id, value]) => {
            const control = element(id);
            if (control) control.value = value;
        });
        const supported = new Set(Object.keys(aggregate?.outputs || {}));
        const preferred = (aggregate?.formats || []).find(format => supported.has(format)) || 'mp3';
        state.export.format = preferred;
        const radio = document.querySelector(`input[name="export-format"][value="${CSS.escape(preferred)}"]`);
        if (radio && !radio.disabled) radio.checked = true;
        state.export.metadataInitialized = true;
    }

    function renderExportPublication(plan, aggregate) {
        const metadata = plan?.metadata || exportMetadataFromControls();
        const title = text(metadata.title, 'Untitled audiobook');
        const author = text(metadata.author, 'Author not entered');
        const narrator = text(metadata.narrator, 'Not entered');
        const set = (id, value) => {
            const target = element(id);
            if (target) target.textContent = value;
        };
        set('export-publication-title', title);
        set('export-publication-author', author);
        set('export-publication-narrator', narrator);
        set('export-publication-cast', narrator === 'Not entered' ? 'Project Cast' : 'Narrator or project Cast');
        const words = title.split(/\s+/).filter(Boolean);
        set('export-cover-initials', words.slice(0, 3).map(word => word.charAt(0)).join('').toUpperCase() || 'A');

        const output = aggregate?.outputs?.[selectedExportFormat()] || {};
        const duration = output.duration_ms
            || aggregate?.player?.duration_ms
            || Math.max(0, ...(plan?.chapters || []).map(chapter => Number(chapter.end_ms || 0)));
        set('export-duration', formatLongDurationMilliseconds(duration));
        set('export-chapter-count', String((plan?.chapters || []).length));
        set('export-size', formatBytes(output.size_bytes) || 'Calculated after build');

        const cover = element('export-cover');
        const coverImage = element('export-cover-image');
        const projectId = state.route.context.project;
        if (cover && coverImage) {
            delete cover.dataset.hasCover;
            coverImage.hidden = true;
            coverImage.removeAttribute('src');
            if (aggregate?.cover?.exists && projectId) {
                coverImage.onload = () => {
                    coverImage.hidden = false;
                    cover.dataset.hasCover = 'true';
                };
                coverImage.onerror = () => {
                    coverImage.hidden = true;
                    coverImage.removeAttribute('src');
                    delete cover.dataset.hasCover;
                };
                coverImage.src = `/api/projects/${encodeURIComponent(projectId)}/cover`;
            }
        }
    }

    function renderExportChapters(plan) {
        const list = element('export-chapter-list');
        if (!list) return;
        const chapters = plan?.chapters || [];
        const visible = state.export.chaptersExpanded ? chapters : chapters.slice(0, 6);
        list.innerHTML = visible.length
            ? visible.map((chapter, index) => `
                <li class="export-chapter-row">
                    <span class="export-chapter-number">${String(index + 1).padStart(2, '0')}</span>
                    <span class="export-chapter-title">${escapeHtml(text(chapter.name, `Chapter ${index + 1}`))}</span>
                    <span class="export-chapter-duration">${escapeHtml(formatLongDurationMilliseconds(Number(chapter.end_ms || 0) - Number(chapter.start_ms || 0)))}</span>
                </li>
            `).join('')
            : '<li class="export-chapter-row"><span class="export-chapter-title">No chapter markers are required for this output.</span></li>';
        const summary = element('export-chapters-summary');
        if (summary) summary.textContent = `${chapters.length.toLocaleString()} ${chapters.length === 1 ? 'chapter' : 'chapters'}`;
        const toggle = element('export-show-chapters');
        if (toggle) {
            toggle.hidden = chapters.length <= 6;
            toggle.textContent = state.export.chaptersExpanded ? 'Show fewer chapters' : `Show all ${chapters.length} chapters`;
        }
    }

    function exportValidationRows(plan, aggregate) {
        const blockers = plan?.blockers || [];
        const hasCode = code => blockers.some(blocker => blocker.code === code);
        const metadataReady = Boolean(plan?.metadata?.title && plan?.metadata?.author);
        const productionReady = !hasCode('export_produce_incomplete');
        const formatReady = !hasCode('export_format_unknown') && !hasCode('export_format_unavailable');
        const chaptersReady = !hasCode('export_chapters_required');
        const processError = text(aggregate?.process?.last_error, '');
        return [
            {
                title: 'Title and author',
                copy: metadataReady ? 'Publication identity is complete.' : 'Enter both title and author.',
                state: metadataReady ? 'success' : 'error',
            },
            {
                title: 'Production audio',
                copy: productionReady ? 'Every required chunk is current and eligible.' : 'Finish or repair Produce before building.',
                state: productionReady ? 'success' : 'error',
            },
            {
                title: exportFormatPresentation(selectedExportFormat()).label,
                copy: formatReady ? exportFormatPresentation(selectedExportFormat()).behavior : 'This output is not available from the current backend.',
                state: formatReady ? 'success' : 'error',
            },
            {
                title: 'Chapter structure',
                copy: chaptersReady
                    ? `${(plan?.chapters || []).length.toLocaleString()} chapter marker${(plan?.chapters || []).length === 1 ? '' : 's'} will be used.`
                    : 'Choose a chaptered structure before building M4B.',
                state: chaptersReady ? 'success' : 'error',
            },
            ...(processError ? [{
                title: 'Previous build attempt',
                copy: `${processError} The previous valid output was preserved.`,
                state: 'error',
            }] : []),
        ];
    }

    function renderExportValidation(plan, aggregate) {
        const list = element('export-validation-list');
        const rows = exportValidationRows(plan, aggregate);
        if (list) {
            list.innerHTML = rows.map(row => `
                <li class="export-validation-row" data-state="${row.state}">
                    <i class="fas ${row.state === 'success' ? 'fa-check' : row.state === 'warning' ? 'fa-triangle-exclamation' : 'fa-xmark'}" data-state="${row.state}" aria-hidden="true"></i>
                    <span><strong>${escapeHtml(row.title)}</strong><small>${escapeHtml(row.copy)}</small><span class="visually-hidden"> ${row.state === 'success' ? 'Ready.' : row.state === 'warning' ? 'Review recommended.' : 'Blocking issue.'}</span></span>
                </li>
            `).join('');
        }
        const errors = rows.filter(row => row.state === 'error').length;
        const summary = element('export-validation-summary');
        if (summary) summary.textContent = errors ? `${errors} blocking ${errors === 1 ? 'issue' : 'issues'}` : 'No blocking issues';

        const technical = element('export-technical-list');
        if (technical) {
            const presentation = exportFormatPresentation(selectedExportFormat());
            const values = [
                ['Selected output', presentation.label],
                ['Filename', plan?.output_filenames?.[selectedExportFormat()] || presentation.filename],
                ['Replacement', 'Validate temporary output, then replace atomically'],
                ['Dependency', text(plan?.dependency_fingerprint, 'Not planned')],
                ['Plan', text(plan?.plan_fingerprint, 'Not planned')],
                ['Receipt', aggregate?.receipt ? 'Recorded' : 'Not built'],
            ];
            technical.innerHTML = values.map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join('');
        }
    }

    function renderExportOutput(plan, aggregate) {
        const format = selectedExportFormat();
        state.export.format = format;
        const presentation = exportFormatPresentation(format);
        const filename = plan?.output_filenames?.[format] || presentation.filename;
        const filenameTarget = element('export-filename');
        const behavior = element('export-filename-behavior');
        if (filenameTarget) {
            if ('value' in filenameTarget) filenameTarget.value = filename;
            else filenameTarget.textContent = filename;
        }
        if (behavior) behavior.textContent = presentation.behavior;
        const folderPath = text(aggregate?.technical_details?.project_path, 'Alexandria project export folder');
        const folder = element('export-folder-path');
        if (folder) folder.textContent = folderPath;

        document.querySelectorAll('input[name="export-format"]').forEach(input => {
            const available = input.value === 'chapter_separated'
                ? false
                : Boolean(aggregate?.outputs?.[input.value]);
            input.disabled = !available;
            input.closest('label')?.toggleAttribute('data-format-unavailable', !available);
        });
    }

    function renderExportProgress(aggregate) {
        const process = aggregate?.process || {};
        const banner = element('export-progress-banner');
        if (!banner) return;
        banner.hidden = !process.running;
        const logs = Array.isArray(process.logs) ? process.logs : [];
        const progress = process.running ? Math.min(88, 20 + logs.length * 12) : 100;
        const title = element('export-progress-title');
        const copy = element('export-progress-copy');
        const bar = element('export-progress-bar');
        if (title) title.textContent = process.cancel ? 'Cancelling build…' : 'Building audiobook…';
        if (copy) copy.textContent = logs.at(-1) || 'Validating and assembling selected outputs.';
        if (bar) bar.style.width = `${progress}%`;
        const root = banner.querySelector('[role="progressbar"]');
        root?.setAttribute('aria-valuenow', String(progress));
        const cancel = element('export-cancel-build');
        if (cancel) cancel.disabled = Boolean(process.cancel);
    }

    function renderExportHeader(plan, aggregate) {
        const workflow = element('shell-workflow-state');
        const action = element('shell-primary-action');
        const process = aggregate?.process || {};
        const priorComplete = aggregate?.summary?.complete === true;
        const hasError = Boolean(process.last_error);
        let label = 'Blocked';
        let stateValue = 'blocked';
        if (process.running) {
            label = 'Building';
            stateValue = 'in_progress';
        } else if (hasError) {
            label = 'Failed';
            stateValue = 'failed';
        } else if (priorComplete) {
            label = 'Built';
            stateValue = 'complete';
        } else if (plan?.safe_to_execute) {
            label = 'Ready to build';
            stateValue = 'ready';
        }
        if (workflow) {
            workflow.textContent = label;
            workflow.dataset.state = stateValue;
        }
        if (action && state.route.destination === 'export') {
            action.hidden = false;
            action.dataset.action = 'export-primary';
            action.textContent = process.running || state.export.building
                ? 'Building audiobook…'
                : 'Build Audiobook';
            action.disabled = process.running || state.export.planning || state.export.building || !plan?.safe_to_execute;
            action.title = action.disabled && !process.running
                ? 'Resolve Final validation issues before building.'
                : '';
        }
    }

    function setPersistentAudioFromExport(aggregate) {
        const player = aggregate?.player;
        if (!player?.url) return;
        const audio = element('main-audio');
        const source = audio?.querySelector('source');
        if (!audio || !source) return;
        const presentation = exportFormatPresentation(player.format);
        if (source.getAttribute('src') !== player.url) {
            source.src = player.url;
            source.type = presentation.mime;
            audio.load();
        }
        const title = element('persistent-player-title');
        const context = element('persistent-player-context');
        if (title) title.textContent = text(exportMetadataFromControls().title, 'Final audiobook');
        if (context) context.textContent = presentation.label;
    }

    function renderExportBuiltConfirmation(aggregate) {
        const confirmation = element('export-built-confirmation');
        if (!confirmation) return;
        const process = aggregate?.process || {};
        const successful = aggregate?.summary?.complete === true
            && !process.last_error
            && process.result?.status !== 'cancelled';
        confirmation.hidden = !successful;
        if (!successful) return;
        const selected = aggregate.selected_outputs?.find(output => output.state === 'current');
        const copy = element('export-built-copy');
        if (copy) {
            copy.textContent = selected
                ? `${selected.filename} is current and ready in the Alexandria project export folder.`
                : 'The selected output is current and ready.';
        }
    }

    function renderExport() {
        const aggregate = state.export.aggregate || {};
        const plan = state.export.plan || aggregate.plan || {};
        renderExportPublication(plan, aggregate);
        renderExportChapters(plan);
        renderExportOutput(plan, aggregate);
        renderExportValidation(plan, aggregate);
        renderExportProgress(aggregate);
        renderExportHeader(plan, aggregate);
        renderExportBuiltConfirmation(aggregate);
        setPersistentAudioFromExport(aggregate);
        const subtitle = element('shell-page-subtitle');
        if (subtitle) {
            subtitle.textContent = aggregate?.process?.running
                ? 'Validating and assembling the selected publication output.'
                : plan?.safe_to_execute
                    ? 'Validate the publication, chapters, output, and final preflight before building.'
                    : 'Resolve the final preflight issues before building the audiobook.';
        }

        const waveform = element('export-waveform');
        if (waveform) {
            const player = aggregate.player;
            const durationSeconds = Math.max(1, Math.round(Number(player?.duration_ms || 0) / 1000));
            waveform.disabled = !player?.url;
            waveform.max = String(durationSeconds);
            waveform.setAttribute('aria-valuemax', String(durationSeconds));
        }
    }

    async function refreshExportPlan({ silent = false } = {}) {
        const request = ++state.export.planRequest;
        state.export.planning = true;
        renderExportHeader(state.export.plan, state.export.aggregate);
        try {
            const plan = await fetchJson('/api/export/plan', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(exportPlanPayload()),
            });
            if (request !== state.export.planRequest) return null;
            state.export.plan = plan;
            renderExport();
            return plan;
        } catch (error) {
            if (request !== state.export.planRequest) return null;
            state.export.plan = null;
            if (!silent) showInlineStatus(`Export could not be planned. ${error.message}`, 'error');
            renderExport();
            return null;
        } finally {
            if (request === state.export.planRequest) {
                state.export.planning = false;
                renderExportHeader(state.export.plan, state.export.aggregate);
            }
        }
    }

    async function loadExport({ force = false } = {}) {
        const request = ++state.export.request;
        const loading = element('export-loading');
        const content = element('export-content');
        if (loading) loading.hidden = false;
        if (content && !state.export.aggregate) content.hidden = true;
        try {
            const aggregate = await fetchJson('/api/export');
            if (request !== state.export.request) return;
            state.export.aggregate = aggregate;
            initializeExportControls(aggregate);
            renderExport();
            if (content) content.hidden = false;
            if (loading) loading.hidden = true;
            await refreshExportPlan({ silent: true });
            if (aggregate.process?.running) startExportPolling();
            else stopExportPolling();
        } catch (error) {
            if (request !== state.export.request) return;
            if (loading) {
                loading.hidden = false;
                loading.innerHTML = `
                    <div class="canonical-error-state" role="alert">
                        <div><strong>Export could not be loaded</strong><p>${escapeHtml(error.message)}</p></div>
                        <button type="button" class="btn btn-outline-secondary" id="export-retry">Retry</button>
                    </div>
                `;
            }
        }
    }

    function startExportPolling() {
        if (state.export.polling) return;
        state.export.polling = window.setInterval(() => {
            if (state.route.destination !== 'export') {
                stopExportPolling();
                return;
            }
            loadExport({ force: true });
        }, 1200);
    }

    function stopExportPolling() {
        if (!state.export.polling) return;
        window.clearInterval(state.export.polling);
        state.export.polling = null;
    }

    async function buildExport() {
        if (state.export.building) return;
        state.export.building = true;
        renderExportHeader(state.export.plan, state.export.aggregate);
        try {
            const plan = await refreshExportPlan();
            if (!plan?.safe_to_execute) {
                throw new Error('Resolve Final validation issues before building.');
            }
            const payload = {
                ...exportPlanPayload(),
                plan_fingerprint: plan.plan_fingerprint,
                dependency_fingerprint: plan.dependency_fingerprint,
            };
            await fetchJson('/api/export/build', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            showInlineStatus('Audiobook build started.', 'success');
            await loadExport({ force: true });
            startExportPolling();
        } catch (error) {
            if (error.status === 409) await refreshExportPlan({ silent: true });
            showInlineStatus(`Audiobook could not be built. ${error.message}`, 'error');
        } finally {
            state.export.building = false;
            renderExportHeader(state.export.plan, state.export.aggregate);
        }
    }

    function setupExport() {
        let planTimer = null;
        const schedulePlan = () => {
            window.clearTimeout(planTimer);
            planTimer = window.setTimeout(() => refreshExportPlan({ silent: true }), 300);
            renderExportPublication(state.export.plan, state.export.aggregate);
        };
        document.querySelectorAll('#export-title, #export-author, #export-narrator, #export-year, #export-description').forEach(control => {
            control.addEventListener('input', schedulePlan);
        });
        document.querySelectorAll('input[name="export-format"]').forEach(input => {
            input.addEventListener('change', () => {
                if (!input.checked) return;
                state.export.format = input.value;
                refreshExportPlan({ silent: true });
            });
        });
        element('export-show-chapters')?.addEventListener('click', () => {
            state.export.chaptersExpanded = !state.export.chaptersExpanded;
            renderExportChapters(state.export.plan || state.export.aggregate?.plan || {});
        });
        element('export-cancel-build')?.addEventListener('click', async () => {
            try {
                await fetchJson('/api/export/cancel', { method: 'POST' });
                await loadExport({ force: true });
            } catch (error) {
                showInlineStatus(`Build could not be cancelled. ${error.message}`, 'error');
            }
        });
        element('export-loading')?.addEventListener('click', event => {
            if (event.target.closest('#export-retry')) loadExport({ force: true });
        });
        const waveform = element('export-waveform');
        const audio = element('main-audio');
        waveform?.addEventListener('input', () => {
            if (audio && !waveform.disabled) audio.currentTime = Number(waveform.value);
        });
        audio?.addEventListener('timeupdate', () => {
            if (waveform && !waveform.disabled) waveform.value = String(Math.round(audio.currentTime || 0));
        });
    }

    function libraryKindPresentation(kind) {
        return {
            source_book: { label: 'Source book', icon: 'fa-book-open' },
            production_audio: { label: 'Production audio', icon: 'fa-wave-square' },
            export_output: { label: 'Finished output', icon: 'fa-arrow-up-from-bracket' },
            built_in: { label: 'Built-in Voice', icon: 'fa-microphone-lines' },
            designed: { label: 'Designed Voice', icon: 'fa-wand-magic-sparkles' },
            supplied_recording: { label: 'Supplied recording', icon: 'fa-wave-square' },
            instruction_controlled: { label: 'Instruction-controlled', icon: 'fa-sliders' },
            adapter: { label: 'Voice adapter', icon: 'fa-layer-group' },
            alias: { label: 'Voice alias', icon: 'fa-link' },
            designed_voice: { label: 'Designed Voice', icon: 'fa-wand-magic-sparkles' },
            clone_reference: { label: 'Clone reference', icon: 'fa-microphone-lines' },
            owned_recording: { label: 'Owned recording', icon: 'fa-file-audio' },
            expressive_reference_bank: { label: 'Reference bank', icon: 'fa-layer-group' },
            voice_preparation_project: { label: 'Voice preparation', icon: 'fa-list-check' },
            preparer_output: { label: 'Prepared audio', icon: 'fa-wave-square' },
            dataset_builder_project: { label: 'Dataset project', icon: 'fa-table-list' },
            lora_dataset: { label: 'Training dataset', icon: 'fa-database' },
            lora_adapter: { label: 'Experimental adapter', icon: 'fa-flask' },
        }[kind] || { label: titleCase(kind), icon: 'fa-file' };
    }

    function formatLibraryDate(value) {
        if (!value) return 'Unknown';
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return 'Unknown';
        return date.toLocaleDateString(undefined, {
            year: 'numeric',
            month: 'short',
            day: 'numeric',
        });
    }

    function libraryContextPayload() {
        return {
            project_id: state.route.context.project || state.flow?.project?.id || null,
            character_id: state.route.context.character || null,
            return_route: state.route.hash || '#/library',
        };
    }

    function voiceLibraryAsInventory(payload) {
        const voices = Array.isArray(payload?.voices) ? payload.voices : [];
        const artifacts = voices.map(voice => ({
            artifact_id: voice.voice_id,
            kind: voice.method,
            name: voice.name,
            state: voice.state,
            size_bytes: voice.technical_details?.size_bytes || 0,
            file_count: voice.technical_details?.file_count || 0,
            modified_at_utc: voice.technical_details?.modified_at_utc || null,
            dependency_count: voice.usage_count || 0,
            blocking_dependency_count: 0,
            usage: voice.usage || [],
            provenance: {
                method: voice.method_label,
                assigned: voice.assigned ? 'Assigned in Cast' : 'Not assigned',
                capability: voice.capability?.message || voice.description,
            },
            metadata_error: voice.technical_details?.metadata_error || null,
            native_route: voice.native_route,
            assignment_route: voice.assignment_route,
            preview: voice.preview,
            delete: { supported: false },
            technical_details: voice.technical_details || {},
            voice_resource: voice,
        }));
        return {
            summary: {
                artifact_count: artifacts.length,
                dependency_count: Number(payload?.summary?.assignment_count || 0),
                invalid_count: Number(payload?.summary?.invalid_voice_count || 0),
                total_size_bytes: artifacts.reduce((total, item) => total + Number(item.size_bytes || 0), 0),
            },
            filters: {
                available_kinds: payload?.filters?.methods || [],
                available_states: payload?.filters?.states || [],
            },
            artifacts,
            methods: payload?.methods || [],
            voice_summary: payload?.summary || {},
            assignment_mutation_supported: payload?.assignment_mutation_supported === true,
            cast_is_authoritative: payload?.cast_is_authoritative === true,
            fingerprint: payload?.fingerprint || null,
        };
    }

    function setPersistentVoicePreview(artifact) {
        const preview = artifact?.preview || artifact?.voice_resource?.preview;
        const audio = element('main-audio');
        if (!preview?.available || !preview.url || !audio) return;
        const source = audio.querySelector('source');
        const extension = String(preview.url).split('?')[0].split('.').pop()?.toLocaleLowerCase();
        const mime = extension === 'mp3'
            ? 'audio/mpeg'
            : extension === 'flac'
                ? 'audio/flac'
                : extension === 'ogg'
                    ? 'audio/ogg'
                    : extension === 'm4a'
                        ? 'audio/mp4'
                        : 'audio/wav';
        if (source) {
            source.src = preview.url;
            source.type = mime;
        } else {
            audio.src = preview.url;
        }
        const title = element('persistent-player-title');
        const context = element('persistent-player-context');
        if (title) title.textContent = preview.title || artifact.name;
        if (context) context.textContent = preview.context || libraryKindPresentation(artifact.kind).label;
        audio.load();
        audio.play().catch(() => {
            showInlineStatus('Voice preview is loaded in the persistent player.', 'info');
        });
    }

    function applyLibraryRouteContext(route) {
        const context = route?.context || {};
        const filter = new URLSearchParams(context.filter || '');
        state.library.query = context.search || '';
        state.library.kind = filter.get('kind') || '';
        state.library.stateFilter = filter.get('state') || '';
        const search = element('library-search');
        if (search && search.value !== state.library.query) search.value = state.library.query;
    }

    function syncLibraryRouteContext() {
        const changes = {};
        const remove = [];
        const query = state.library.query.trim();
        if (query) changes.search = query;
        else remove.push('search');
        const filter = new URLSearchParams();
        if (state.library.kind) filter.set('kind', state.library.kind);
        if (state.library.stateFilter) filter.set('state', state.library.stateFilter);
        const filterValue = filter.toString();
        if (filterValue) changes.filter = filterValue;
        else remove.push('filter');
        const updatedRoute = window.AlexandriaNavigation?.updateContext(changes, {
            historyMode: 'replace',
            remove,
        });
        if (updatedRoute) state.route = updatedRoute;
    }

    function libraryNativeActionLabel(artifact) {
        const destination = artifact?.native_route?.destination;
        if (destination === 'script') return 'Open Script';
        if (destination === 'produce') return 'Open Produce';
        if (destination === 'export') return 'Open Export';
        if (destination === 'more') return 'Open Voice Lab';
        return 'Open native destination';
    }

    function visibleLibraryArtifacts() {
        const query = state.library.query.trim().toLocaleLowerCase();
        const voicesOnly = state.route.destination === 'voices';
        return (state.library.inventory?.artifacts || []).filter(artifact => {
            if (voicesOnly && !VOICE_LIBRARY_KINDS.has(artifact.kind)) return false;
            if (state.library.kind && artifact.kind !== state.library.kind) return false;
            if (state.library.stateFilter && artifact.state !== state.library.stateFilter) return false;
            if (!query) return true;
            return [
                artifact.name,
                artifact.kind,
                artifact.state,
                artifact.character_id,
                ...Object.values(artifact.provenance || {}),
            ].some(value => String(value || '').toLocaleLowerCase().includes(query));
        });
    }

    function populateLibraryFilters() {
        const inventory = state.library.inventory || {};
        const kinds = (inventory.filters?.available_kinds || []).filter(kind => (
            state.route.destination !== 'voices' || VOICE_LIBRARY_KINDS.has(kind)
        ));
        const kindSelect = element('library-kind-filter');
        if (kindSelect) {
            const selected = kinds.includes(state.library.kind) ? state.library.kind : '';
            if (selected !== state.library.kind) state.library.kind = selected;
            kindSelect.innerHTML = [
                '<option value="">All material</option>',
                ...kinds.map(kind => `<option value="${escapeHtml(kind)}">${escapeHtml(libraryKindPresentation(kind).label)}</option>`),
            ].join('');
            kindSelect.value = selected;
        }
        const states = inventory.filters?.available_states || [];
        const stateSelect = element('library-state-filter');
        if (stateSelect) {
            const selected = states.includes(state.library.stateFilter) ? state.library.stateFilter : '';
            if (selected !== state.library.stateFilter) state.library.stateFilter = selected;
            stateSelect.innerHTML = [
                '<option value="">Any state</option>',
                ...states.map(item => `<option value="${escapeHtml(item)}">${escapeHtml(titleCase(item))}</option>`),
            ].join('');
            stateSelect.value = selected;
        }
    }

    function renderVoiceLibraryDetail(artifact) {
        const detail = element('library-artifact-detail');
        if (!detail) return;
        if (!artifact) {
            detail.innerHTML = `
                <div class="canonical-empty-state">
                    <span class="canonical-empty-mark" aria-hidden="true"><i class="fas fa-microphone-lines"></i></span>
                    <div><strong>No Voice selected</strong><p>Select a reusable Voice to inspect method capability, listening entry points, and current Cast usage.</p></div>
                </div>
            `;
            return;
        }
        const voice = artifact.voice_resource || {};
        const capability = voice.capability || {};
        const usage = Array.isArray(voice.usage) ? voice.usage : [];
        const preview = voice.preview || {};
        const assignedCopy = usage.length
            ? `${usage.length} Cast assignment${usage.length === 1 ? '' : 's'}`
            : 'Not assigned in Cast';
        detail.innerHTML = `
            <div class="library-detail-header">
                <span class="supporting-row-icon" aria-hidden="true"><i class="fas ${libraryKindPresentation(artifact.kind).icon}"></i></span>
                <div>
                    <span class="canonical-kicker">${escapeHtml(voice.method_label || libraryKindPresentation(artifact.kind).label)}</span>
                    <h2>${escapeHtml(artifact.name)}</h2>
                    <p>${escapeHtml(voice.description || '')}</p>
                </div>
                <span class="canonical-status-chip" data-state="${escapeHtml(artifact.state)}">${escapeHtml(titleCase(artifact.state))}</span>
            </div>
            <div class="library-detail-actions">
                ${preview.available ? `<button type="button" class="btn btn-outline-secondary" data-voice-preview="${escapeHtml(artifact.artifact_id)}"><i class="fas fa-play" aria-hidden="true"></i> Listen</button>` : ''}
                <button type="button" class="btn btn-outline-secondary" data-library-open="${escapeHtml(artifact.artifact_id)}">${escapeHtml(libraryNativeActionLabel(artifact))}</button>
                <button type="button" class="btn btn-outline-secondary" data-voice-cast="${escapeHtml(artifact.artifact_id)}">${usage.length === 1 ? 'Open Cast assignment' : 'Open Cast'}</button>
            </div>
            <section class="library-detail-section">
                <span class="canonical-kicker">Method capability</span>
                <dl class="canonical-definition-grid">
                    <div><dt>Production</dt><dd>${capability.production_supported ? 'Supported' : 'Not approved'}</dd></div>
                    <div><dt>Preview</dt><dd>${capability.preview_supported ? 'Available' : 'Unavailable'}</dd></div>
                    <div><dt>Line instruction</dt><dd>${capability.instruction_supported ? 'Channel present' : 'Not supported'}</dd></div>
                    <div><dt>Usage</dt><dd>${escapeHtml(assignedCopy)}</dd></div>
                </dl>
                <p class="canonical-inline-note">${escapeHtml(capability.message || '')}</p>
            </section>
            <section class="library-detail-section">
                <span class="canonical-kicker">Used by</span>
                ${usage.length ? `
                    <div class="library-usage-list">
                        ${usage.map(item => `
                            <button type="button" class="library-usage-row" data-voice-cast-character="${escapeHtml(item.character_id || '')}">
                                <span><strong>${escapeHtml(item.character_name || item.script_label || 'Character')}</strong><small>${escapeHtml(item.script_label || 'No Script label')}</small></span>
                                <span>${item.valid ? 'Valid Voice' : 'Needs attention'}</span>
                            </button>
                        `).join('')}
                    </div>
                ` : '<p class="canonical-muted">This reusable Voice is not assigned. Assignment happens only in Cast.</p>'}
            </section>
            <details class="library-technical-details">
                <summary>Technical details</summary>
                <dl class="canonical-definition-grid">
                    <div><dt>Method</dt><dd>${escapeHtml(voice.method || artifact.kind)}</dd></div>
                    <div><dt>Capability state</dt><dd>${escapeHtml(capability.state || artifact.state)}</dd></div>
                    <div><dt>Source files</dt><dd>${escapeHtml(String(artifact.file_count || 0))}</dd></div>
                    <div><dt>Size</dt><dd>${escapeHtml(formatBytes(artifact.size_bytes || 0))}</dd></div>
                </dl>
            </details>
            <p class="canonical-inline-note">Voices is read-only. Saving or changing a production Voice remains a Cast action.</p>
        `;
    }

    function renderLibraryDetail(artifact) {
        const detail = element('library-artifact-detail');
        if (!detail) return;
        if (state.route.destination === 'voices') {
            renderVoiceLibraryDetail(artifact);
            return;
        }
        if (!artifact) {
            detail.innerHTML = `
                <div class="canonical-empty-state">
                    <span class="canonical-empty-mark" aria-hidden="true"><i class="fas fa-book-open"></i></span>
                    <div><strong>No Library material selected</strong><p>Adjust the filters or select an item to inspect its usage and native destination.</p></div>
                </div>
            `;
            return;
        }
        const kind = libraryKindPresentation(artifact.kind);
        const usage = artifact.usage || [];
        const provenance = Object.entries(artifact.provenance || {});
        const relativePaths = artifact.technical_details?.relative_paths
            || (artifact.technical_details?.relative_path ? [artifact.technical_details.relative_path] : []);
        const deleteState = artifact.delete || {};
        const deletionCopy = deleteState.supported
            ? deleteState.blocked
                ? text(deleteState.reason, 'Dependencies block deletion.')
                : 'Deletion is available after one more dependency review.'
            : 'This material has no authoritative deletion route.';
        detail.innerHTML = `
            <header class="supporting-detail-header">
                <div>
                    <span class="canonical-kicker">${escapeHtml(kind.label)}</span>
                    <h2>${escapeHtml(text(artifact.name, 'Unnamed material'))}</h2>
                    <p class="help-topic-meta">${escapeHtml(titleCase(artifact.state))}${artifact.character_id ? ` · Character material` : ''}</p>
                </div>
                <div class="supporting-detail-actions">
                    <button type="button" class="btn btn-outline-secondary" data-library-open="${escapeHtml(artifact.artifact_id)}">${escapeHtml(libraryNativeActionLabel(artifact))}</button>
                    <button type="button" class="btn btn-outline-danger" data-library-delete="${escapeHtml(artifact.artifact_id)}" ${deleteState.supported && !deleteState.blocked ? '' : 'disabled'} title="${escapeHtml(deletionCopy)}">Delete</button>
                </div>
            </header>
            <dl class="supporting-facts">
                <div><dt>State</dt><dd>${escapeHtml(titleCase(artifact.state))}</dd></div>
                <div><dt>Type</dt><dd>${escapeHtml(kind.label)}</dd></div>
                <div><dt>Size</dt><dd>${escapeHtml(formatBytes(artifact.size_bytes) || 'No stored bytes')}</dd></div>
                <div><dt>Files</dt><dd>${Number(artifact.file_count || 0).toLocaleString()}</dd></div>
                <div><dt>Modified</dt><dd>${escapeHtml(formatLibraryDate(artifact.modified_at_utc))}</dd></div>
                <div><dt>Dependencies</dt><dd>${Number(artifact.dependency_count || 0).toLocaleString()}</dd></div>
            </dl>
            <section class="supporting-section">
                <h3>Current usage</h3>
                ${usage.length ? `
                    <ul class="supporting-usage-list">
                        ${usage.map(item => `<li><strong>${escapeHtml(titleCase(item.native_destination || item.scope))}</strong><small>${escapeHtml(text(item.source, 'Current project'))}</small></li>`).join('')}
                    </ul>
                ` : '<p class="text-muted mb-0">No current project dependency was found.</p>'}
            </section>
            ${provenance.length ? `
                <section class="supporting-section">
                    <h3>Provenance</h3>
                    <dl class="supporting-facts">
                        ${provenance.map(([key, value]) => `<div><dt>${escapeHtml(titleCase(key))}</dt><dd>${escapeHtml(String(value))}</dd></div>`).join('')}
                    </dl>
                </section>
            ` : ''}
            <section class="supporting-section">
                <h3>Deletion</h3>
                <p class="mb-0">${escapeHtml(deletionCopy)}</p>
            </section>
            <details class="script-review-disclosure supporting-section">
                <summary><span>Technical details</span><i class="fas fa-chevron-right" aria-hidden="true"></i></summary>
                <div class="script-review-disclosure-body">
                    <dl class="supporting-facts">
                        <div><dt>Artifact ID</dt><dd>${escapeHtml(artifact.artifact_id)}</dd></div>
                        <div><dt>Fingerprint</dt><dd>${escapeHtml(artifact.fingerprint)}</dd></div>
                        <div><dt>Project path</dt><dd>${escapeHtml(relativePaths.length ? relativePaths.join(', ') : 'Not available')}</dd></div>
                    </dl>
                </div>
            </details>
        `;
    }

    function configureLibrarySurface(mode) {
        const voicesMode = mode === 'voices';
        const search = element('library-search');
        const heading = element('library-list-heading');
        const refresh = element('library-refresh');
        const list = element('library-artifact-list');
        const kicker = heading?.previousElementSibling;
        if (search) {
            search.placeholder = voicesMode ? 'Search Voices…' : 'Search Library…';
            search.setAttribute('aria-label', voicesMode ? 'Search Voices' : 'Search Library');
        }
        if (heading) heading.textContent = voicesMode ? 'Reusable Voices' : 'Project material';
        if (kicker) kicker.textContent = voicesMode ? 'Voice library' : 'Active project';
        if (refresh) {
            refresh.setAttribute('aria-label', voicesMode ? 'Refresh Voices' : 'Refresh Library');
            refresh.title = voicesMode ? 'Refresh Voices' : 'Refresh Library';
        }
        if (list) list.setAttribute('aria-label', voicesMode ? 'Reusable Voices' : 'Library artifacts');
    }

    function renderLibrary() {
        populateLibraryFilters();
        const artifacts = visibleLibraryArtifacts();
        if (artifacts.length && !artifacts.some(item => item.artifact_id === state.library.selectedId)) {
            state.library.selectedId = artifacts[0].artifact_id;
        }
        if (!artifacts.length) state.library.selectedId = null;
        const list = element('library-artifact-list');
        if (list) {
            list.innerHTML = artifacts.length
                ? artifacts.map(artifact => {
                    const presentation = libraryKindPresentation(artifact.kind);
                    const selected = artifact.artifact_id === state.library.selectedId;
                    return `
                        <button type="button" class="supporting-list-row" id="library-artifact-${escapeHtml(artifact.artifact_id)}" role="option" aria-selected="${selected}" tabindex="${selected ? '0' : '-1'}" data-library-artifact="${escapeHtml(artifact.artifact_id)}" data-library-kind="${escapeHtml(artifact.kind)}">
                            <span class="supporting-list-icon"><i class="fas ${presentation.icon}" aria-hidden="true"></i></span>
                            <span class="supporting-list-copy"><strong>${escapeHtml(text(artifact.name, 'Unnamed material'))}</strong><small>${escapeHtml(presentation.label)} · ${Number(artifact.dependency_count || 0).toLocaleString()} ${Number(artifact.dependency_count || 0) === 1 ? 'dependency' : 'dependencies'}</small></span>
                            <span class="supporting-state" data-state="${escapeHtml(artifact.state)}">${escapeHtml(titleCase(artifact.state))}</span>
                        </button>
                    `;
                }).join('')
                : Number(state.library.inventory?.summary?.artifact_count || 0) === 0
                    ? `
                        <div class="canonical-empty-state">
                            <span class="canonical-empty-mark" aria-hidden="true"><i class="fas fa-book-open"></i></span>
                            <div><strong>No Library material yet</strong><p>Open or create a project to inventory its source, audio, Voice material, training assets, and outputs.</p></div>
                        </div>
                    `
                    : `
                        <div class="canonical-empty-state">
                            <span class="canonical-empty-mark" aria-hidden="true"><i class="fas fa-filter-circle-xmark"></i></span>
                            <div><strong>No material matches these filters</strong><p>Clear the search or choose a broader type and state.</p></div>
                        </div>
                    `;
        }
        if (list) {
            if (state.library.selectedId) {
                list.setAttribute(
                    'aria-activedescendant',
                    `library-artifact-${state.library.selectedId}`
                );
            } else {
                list.removeAttribute('aria-activedescendant');
            }
        }
        const count = element('library-result-count');
        if (count) count.textContent = `${artifacts.length.toLocaleString()} of ${Number(state.library.inventory?.summary?.artifact_count || 0).toLocaleString()}`;
        renderLibraryDetail(artifacts.find(item => item.artifact_id === state.library.selectedId) || null);
    }

    async function loadLibrary({ force = false } = {}) {
        configureLibrarySurface('library');
        const loadingCopy = element('library-loading-copy');
        if (loadingCopy) loadingCopy.textContent = 'Reading the active project inventory…';
        const request = ++state.library.request;
        const loading = element('library-loading');
        const content = element('library-content');
        if (!force && state.library.inventory && state.library.mode === 'library') {
            renderLibrary();
            return;
        }
        if (loading) loading.hidden = false;
        if (content) content.hidden = true;
        try {
            const params = new URLSearchParams();
            const context = libraryContextPayload();
            if (context.project_id) params.set('project_id', context.project_id);
            if (context.character_id) params.set('character_id', context.character_id);
            params.set('return_route', context.return_route);
            const inventory = await fetchJson(`/api/library?${params.toString()}`);
            if (request !== state.library.request) return;
            state.library.inventory = inventory;
            state.library.mode = 'library';
            renderLibrary();
            if (loading) loading.hidden = true;
            if (content) content.hidden = false;
        } catch (error) {
            if (request !== state.library.request) return;
            if (loading) {
                loading.hidden = false;
                loading.innerHTML = `
                    <div class="canonical-error-state" role="alert">
                        <div><strong>Library could not be loaded</strong><p>${escapeHtml(error.message)}</p></div>
                        <button type="button" class="btn btn-outline-secondary" id="library-retry">Retry</button>
                    </div>
                `;
            }
        }
    }

    async function loadVoices(options = {}) {
        configureLibrarySurface('voices');
        const request = ++state.voices.request;
        const loading = element('library-loading');
        const content = element('library-content');
        const loadingCopy = element('library-loading-copy');
        if (loadingCopy) loadingCopy.textContent = 'Reading reusable Voices and Cast usage…';
        if (!options.silent) {
            if (loading) loading.hidden = false;
            if (content) content.hidden = true;
        }
        try {
            const context = libraryContextPayload();
            const params = new URLSearchParams();
            if (context.project_id) params.set('project_id', context.project_id);
            params.set('return_route', context.return_route);
            const payload = await fetchJson(`/api/voice-library?${params.toString()}`);
            if (request !== state.voices.request) return;
            state.voices.aggregate = payload;
            state.library.inventory = voiceLibraryAsInventory(payload);
            state.library.mode = 'voices';
            const voices = state.library.inventory.artifacts || [];
            if (!voices.some(item => item.artifact_id === state.library.selectedId)) {
                const requested = state.route.context.voice;
                state.library.selectedId = voices.find(item => item.artifact_id === requested)?.artifact_id
                    || voices.find(item => item.dependency_count > 0)?.artifact_id
                    || voices[0]?.artifact_id
                    || null;
            }
            renderLibrary();
            if (loading) loading.hidden = true;
            if (content) content.hidden = false;
        } catch (error) {
            if (request !== state.voices.request) return;
            if (loading) loading.hidden = true;
            if (content) {
                content.hidden = false;
                content.innerHTML = `
                    <div class="canonical-error-state" role="alert">
                        <div><strong>Voices could not be loaded</strong><p>${escapeHtml(error.message)}</p></div>
                        <button type="button" class="btn btn-outline-secondary" data-library-retry>Retry</button>
                    </div>
                `;
            }
        }
    }

    function openLibraryArtifact(artifactId) {
        const artifact = (state.library.inventory?.artifacts || []).find(item => item.artifact_id === artifactId);
        const route = artifact?.native_route;
        if (!route?.destination) return;
        window.AlexandriaNavigation?.navigate(route.destination, {
            ...(route.context || {}),
            return: state.route.hash,
        });
    }

    async function deleteLibraryArtifact(artifactId) {
        if (state.library.deleting) return;
        const artifact = (state.library.inventory?.artifacts || []).find(item => item.artifact_id === artifactId);
        if (!artifact) return;
        state.library.deleting = true;
        try {
            const context = libraryContextPayload();
            const impact = await fetchJson(`/api/library/artifacts/${encodeURIComponent(artifactId)}/delete-impact`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(context),
            });
            if (!impact.safe_to_delete) {
                showInlineStatus(text(impact.reason, 'This material cannot be deleted safely.'), 'warning');
                return;
            }
            const confirmed = await window.showConfirm?.(
                `Delete ${impact.name}? This material will be removed through its authoritative delete route and cannot be restored from Library.`
            );
            if (!confirmed) return;
            await fetchJson(`/api/library/artifacts/${encodeURIComponent(artifactId)}`, {
                method: 'DELETE',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    ...context,
                    expected_inventory_fingerprint: impact.inventory_fingerprint,
                    expected_artifact_fingerprint: impact.artifact_fingerprint,
                    confirm_name: impact.confirm_name,
                }),
            });
            state.library.inventory = null;
            state.library.selectedId = null;
            showInlineStatus(`${impact.name} was deleted.`, 'success');
            await loadLibrary({ force: true });
        } catch (error) {
            if (error.status === 409) state.library.inventory = null;
            showInlineStatus(`Library material was not deleted. ${error.message}`, 'error');
            if (!state.library.inventory) await loadLibrary({ force: true });
        } finally {
            state.library.deleting = false;
        }
    }

    function templateMethodLabel(value) {
        return {
            local: 'Local generation',
            chatgpt_task_bundle: 'ChatGPT Task Bundle',
            import_existing_script: 'Import Alexandria Script',
        }[value] || titleCase(value);
    }

    function templatePresetLabel(value) {
        return {
            standard: 'Standard',
            maximum_fidelity: 'Maximum fidelity',
            faster_draft: 'Faster draft',
            custom: 'Custom',
        }[value] || titleCase(value);
    }

    function applyTemplateRouteContext(route) {
        const context = route?.context || {};
        const filterValue = String(context.filter || '').trim();
        const composite = new URLSearchParams(filterValue);
        state.templates.query = context.search || '';
        state.templates.scope = ['built_in', 'custom'].includes(filterValue)
            ? filterValue
            : composite.get('scope') || 'all';
        state.templates.selectedId = context.template || state.templates.selectedId;
        const search = element('template-search');
        const scope = element('template-scope-filter');
        if (search && search.value !== state.templates.query) search.value = state.templates.query;
        if (scope && scope.value !== state.templates.scope) scope.value = state.templates.scope;
    }

    function syncTemplateRouteContext({ selectedId = state.templates.selectedId } = {}) {
        const changes = {};
        const remove = [];
        const query = state.templates.query.trim();
        if (query) changes.search = query;
        else remove.push('search');
        if (state.templates.scope !== 'all') {
            changes.filter = state.templates.scope;
        } else {
            remove.push('filter');
        }
        if (selectedId) changes.template = selectedId;
        else remove.push('template');
        const updated = window.AlexandriaNavigation?.updateContext(changes, {
            historyMode: 'replace',
            remove,
        });
        if (updated) state.route = updated;
    }

    function filteredTemplates() {
        const catalog = state.templates.catalog;
        const query = state.templates.query.trim().toLocaleLowerCase();
        return (catalog?.templates || []).filter(template => {
            if (state.templates.scope === 'built_in' && !template.built_in) return false;
            if (state.templates.scope === 'custom' && template.built_in) return false;
            if (!query) return true;
            return [
                template.name,
                template.description,
                template.intent,
                template.generation_method,
                template.preset,
                template.source_language,
                template.output_language,
            ].some(value => String(value || '').toLocaleLowerCase().includes(query));
        });
    }

    function selectedTemplate() {
        return (state.templates.catalog?.templates || []).find(
            template => template.id === state.templates.selectedId
        ) || null;
    }

    function renderTemplateDetail(template) {
        const detail = element('template-detail');
        if (!detail) return;
        if (!template) {
            detail.innerHTML = `
                <div class="canonical-empty-state">
                    <span class="canonical-empty-mark" aria-hidden="true"><i class="fas fa-copy"></i></span>
                    <div><strong>No template selected</strong><p>Adjust the search or select a template to inspect its project setup and actions.</p></div>
                </div>
            `;
            return;
        }
        const custom = !template.built_in;
        detail.innerHTML = `
            <header class="supporting-detail-header">
                <div>
                    <span class="canonical-kicker">${template.built_in ? 'Built-in template' : 'Custom template'}</span>
                    <h2>${escapeHtml(template.name)}</h2>
                    <p class="help-topic-meta">${escapeHtml(template.intent)}${template.default ? ' · Default' : ''}</p>
                </div>
                <div class="supporting-detail-actions">
                    <button type="button" class="btn btn-outline-secondary" data-template-use="${escapeHtml(template.id)}">Use Template</button>
                    ${custom ? `<button type="button" class="btn btn-outline-secondary" data-template-edit="${escapeHtml(template.id)}">Edit</button>` : ''}
                    <button type="button" class="btn btn-outline-secondary" data-template-duplicate="${escapeHtml(template.id)}">Duplicate</button>
                </div>
            </header>
            <div class="template-detail-summary">
                <p class="template-detail-intent">${escapeHtml(text(template.description, 'No additional description.'))}</p>
                <dl class="supporting-facts">
                    <div><dt>Generation method</dt><dd>${escapeHtml(templateMethodLabel(template.generation_method))}</dd></div>
                    <div><dt>Preset</dt><dd>${escapeHtml(templatePresetLabel(template.preset))}</dd></div>
                    <div><dt>Source language</dt><dd>${escapeHtml(template.source_language)}</dd></div>
                    <div><dt>Output language</dt><dd>${escapeHtml(template.output_language)}</dd></div>
                    <div><dt>Scope</dt><dd>${template.built_in ? 'Built-in' : 'Custom'}</dd></div>
                    <div><dt>Default</dt><dd>${template.default ? 'Yes' : 'No'}</dd></div>
                </dl>
            </div>
            <section class="supporting-section">
                <h3>Template actions</h3>
                <div class="supporting-detail-actions justify-content-start">
                    ${template.default
                        ? '<span class="template-default-mark"><i class="fas fa-check" aria-hidden="true"></i> Current default</span>'
                        : `<button type="button" class="btn btn-outline-secondary" data-template-default="${escapeHtml(template.id)}">Make default</button>`}
                    ${custom ? `<button type="button" class="btn btn-outline-danger" data-template-delete="${escapeHtml(template.id)}">Delete template</button>` : ''}
                </div>
            </section>
            <section class="supporting-section">
                <h3>What is not stored</h3>
                <p class="mb-0">Runtime model names, prompts, context limits, cache locations, and credentials remain in Settings or specialist configuration. They are not template fields.</p>
            </section>
        `;
    }

    function renderTemplates() {
        const templates = filteredTemplates();
        if (templates.length && !templates.some(item => item.id === state.templates.selectedId)) {
            state.templates.selectedId = templates[0].id;
            syncTemplateRouteContext();
        }
        if (!templates.length) state.templates.selectedId = null;
        const list = element('template-list');
        if (list) {
            list.innerHTML = templates.length ? templates.map(template => {
                const selected = template.id === state.templates.selectedId;
                return `
                <button type="button" class="supporting-list-row" id="template-row-${escapeHtml(template.id)}" role="option" aria-selected="${selected}" tabindex="${selected ? '0' : '-1'}" data-template-id="${escapeHtml(template.id)}">
                    <span class="supporting-list-icon"><i class="fas ${template.built_in ? 'fa-bookmark' : 'fa-copy'}" aria-hidden="true"></i></span>
                    <span class="supporting-list-copy">
                        <strong>${escapeHtml(template.name)}</strong>
                        <small class="template-list-row-meta"><span>${escapeHtml(templateMethodLabel(template.generation_method))}</span><span>${escapeHtml(templatePresetLabel(template.preset))}</span><span>${escapeHtml(template.output_language)}</span></small>
                    </span>
                    <span class="supporting-state" data-state="${template.default ? 'complete' : template.built_in ? 'available' : 'current'}">${template.default ? 'Default' : template.built_in ? 'Built-in' : 'Custom'}</span>
                </button>
            `;
            }).join('') : `
                <div class="canonical-empty-state">
                    <span class="canonical-empty-mark" aria-hidden="true"><i class="fas fa-filter-circle-xmark"></i></span>
                    <div><strong>No templates match</strong><p>Clear the search or choose another scope.</p></div>
                </div>
            `;
        }
        if (list) {
            if (state.templates.selectedId) {
                list.setAttribute(
                    'aria-activedescendant',
                    `template-row-${state.templates.selectedId}`
                );
            } else {
                list.removeAttribute('aria-activedescendant');
            }
        }
        const count = element('template-result-count');
        if (count) count.textContent = `${templates.length.toLocaleString()} of ${Number(state.templates.catalog?.summary?.template_count || 0).toLocaleString()}`;
        renderTemplateDetail(selectedTemplate());
    }

    async function loadTemplates({ force = false } = {}) {
        const request = ++state.templates.request;
        const loading = element('template-loading');
        const content = element('template-content');
        if (!force && state.templates.catalog) {
            renderTemplates();
            return;
        }
        if (loading) loading.hidden = false;
        if (content) content.hidden = true;
        try {
            const catalog = await fetchJson('/api/templates');
            if (request !== state.templates.request) return;
            state.templates.catalog = catalog;
            const requested = state.route.context.template;
            state.templates.selectedId = catalog.templates.some(item => item.id === requested)
                ? requested
                : state.templates.selectedId || catalog.default_template_id || catalog.templates[0]?.id || null;
            renderTemplates();
            if (loading) loading.hidden = true;
            if (content) content.hidden = false;
        } catch (error) {
            if (request !== state.templates.request) return;
            if (loading) loading.hidden = true;
            if (content) {
                content.hidden = false;
                content.innerHTML = `<div class="canonical-error-state" role="alert"><div><strong>Templates could not be loaded</strong><p>${escapeHtml(error.message)}</p></div><button type="button" class="btn btn-outline-secondary" id="template-retry">Retry</button></div>`;
            }
        }
    }

    function setTemplateEditorStatus(message = '', stateValue = 'info') {
        const notice = element('template-editor-status');
        const copy = element('template-editor-status-copy');
        if (!notice || !copy) return;
        notice.hidden = !message;
        notice.dataset.state = stateValue;
        copy.textContent = message;
    }

    function updateTemplateEditorMethodRules() {
        const method = element('template-editor-method')?.value;
        const preset = element('template-editor-preset');
        if (!preset) return;
        const importing = method === 'import_existing_script';
        if (importing) preset.value = 'standard';
        preset.disabled = importing;
    }

    function templateEditorPayload() {
        return {
            name: element('template-editor-name')?.value.trim() || '',
            description: element('template-editor-description-field')?.value.trim() || '',
            generation_method: element('template-editor-method')?.value || 'local',
            preset: element('template-editor-preset')?.value || 'standard',
            source_language: element('template-editor-source-language')?.value.trim() || '',
            output_language: element('template-editor-output-language')?.value.trim() || '',
            intent: element('template-editor-intent')?.value.trim() || '',
        };
    }

    function openTemplateEditor(template = null) {
        const modalElement = element('templateEditorModal');
        const form = element('template-editor-form');
        if (!modalElement || !form) return;
        state.templates.editingId = template?.id || null;
        element('template-editor-title').textContent = template ? 'Edit Template' : 'New Template';
        element('template-editor-submit').textContent = template ? 'Save Changes' : 'Save Template';
        element('template-editor-name').value = template?.name || '';
        element('template-editor-intent').value = template?.intent || '';
        element('template-editor-description-field').value = template?.description || '';
        element('template-editor-method').value = template?.generation_method || 'local';
        element('template-editor-preset').value = template?.preset || 'standard';
        element('template-editor-source-language').value = template?.source_language || 'English';
        element('template-editor-output-language').value = template?.output_language || 'English';
        setTemplateEditorStatus();
        element('template-editor-footer-state').textContent = 'Ready to save.';
        updateTemplateEditorMethodRules();
        bootstrap.Modal.getOrCreateInstance(modalElement).show();
    }

    async function saveTemplateEditor() {
        if (state.templates.saving) return;
        const form = element('template-editor-form');
        if (!form?.reportValidity()) return;
        const catalog = state.templates.catalog;
        if (!catalog?.catalog_fingerprint) {
            setTemplateEditorStatus('Template catalog is unavailable. Reload Templates and try again.', 'error');
            return;
        }
        const existing = selectedTemplate();
        if (state.templates.editingId && (!existing || existing.id !== state.templates.editingId)) {
            setTemplateEditorStatus('The selected template changed. Reload Templates and try again.', 'error');
            return;
        }
        state.templates.saving = true;
        const submit = element('template-editor-submit');
        if (submit) submit.disabled = true;
        element('template-editor-footer-state').textContent = state.templates.editingId ? 'Saving changes…' : 'Creating template…';
        try {
            const editing = Boolean(state.templates.editingId);
            const payload = {
                expected_catalog_fingerprint: catalog.catalog_fingerprint,
                template: templateEditorPayload(),
                ...(editing ? { expected_template_fingerprint: existing.fingerprint } : {}),
            };
            const result = await fetchJson(
                editing ? `/api/templates/${encodeURIComponent(state.templates.editingId)}` : '/api/templates',
                {
                    method: editing ? 'PUT' : 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                }
            );
            state.templates.catalog = result;
            state.templates.selectedId = result.template?.id || state.templates.selectedId;
            bootstrap.Modal.getOrCreateInstance(element('templateEditorModal')).hide();
            syncTemplateRouteContext();
            renderTemplates();
            showInlineStatus(editing ? 'Template changes saved.' : 'Template created.', 'success');
        } catch (error) {
            if (error.status === 409) state.templates.catalog = null;
            setTemplateEditorStatus(error.message, 'error');
            element('template-editor-footer-state').textContent = 'Template was not saved.';
        } finally {
            state.templates.saving = false;
            if (submit) submit.disabled = false;
        }
    }

    async function duplicateTemplate(template) {
        const name = await window.showTextPrompt?.({
            title: 'Duplicate template',
            description: 'Create an editable custom copy. Existing templates and projects are unchanged.',
            label: 'New template name',
            value: `${template.name} copy`,
            confirmLabel: 'Duplicate',
        });
        if (!name) return;
        try {
            const result = await fetchJson(`/api/templates/${encodeURIComponent(template.id)}/duplicate`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    expected_catalog_fingerprint: state.templates.catalog.catalog_fingerprint,
                    name,
                }),
            });
            state.templates.catalog = result;
            state.templates.selectedId = result.template?.id || state.templates.selectedId;
            syncTemplateRouteContext();
            renderTemplates();
            showInlineStatus('Template duplicated.', 'success');
        } catch (error) {
            if (error.status === 409) state.templates.catalog = null;
            showInlineStatus(`Template was not duplicated. ${error.message}`, 'error');
            if (!state.templates.catalog) await loadTemplates({ force: true });
        }
    }

    async function setDefaultTemplate(template) {
        try {
            const result = await fetchJson(`/api/templates/${encodeURIComponent(template.id)}/default`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    expected_catalog_fingerprint: state.templates.catalog.catalog_fingerprint,
                }),
            });
            state.templates.catalog = result;
            state.templates.selectedId = template.id;
            renderTemplates();
            showInlineStatus(`${template.name} is now the default template.`, 'success');
        } catch (error) {
            if (error.status === 409) state.templates.catalog = null;
            showInlineStatus(`Default template was not changed. ${error.message}`, 'error');
            if (!state.templates.catalog) await loadTemplates({ force: true });
        }
    }

    async function deleteTemplate(template) {
        try {
            const impact = await fetchJson(`/api/templates/${encodeURIComponent(template.id)}/delete-impact`);
            if (!impact.safe_to_delete) {
                showInlineStatus(text(impact.blocking_reasons?.[0]?.message, 'This template cannot be deleted yet.'), 'warning');
                return;
            }
            if (impact.requires_usage_acknowledgement) {
                const acknowledged = await window.showConfirm?.(
                    `${impact.usage_count} existing project${impact.usage_count === 1 ? '' : 's'} used this template. Their saved settings will not change. Continue to deletion confirmation?`
                );
                if (!acknowledged) return;
            }
            const confirmation = await window.showTextPrompt?.({
                title: 'Delete template',
                description: `Type “${impact.confirmation_text}” exactly. Existing projects are not rewritten.`,
                label: 'Template name',
                confirmLabel: 'Delete Template',
            });
            if (confirmation === null) return;
            const result = await fetchJson(`/api/templates/${encodeURIComponent(template.id)}`, {
                method: 'DELETE',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    expected_catalog_fingerprint: impact.catalog_fingerprint,
                    expected_template_fingerprint: impact.template.fingerprint,
                    confirmation_text: confirmation,
                    acknowledge_usage: Boolean(impact.requires_usage_acknowledgement),
                }),
            });
            state.templates.catalog = result;
            state.templates.selectedId = result.default_template_id || result.templates[0]?.id || null;
            syncTemplateRouteContext();
            renderTemplates();
            showInlineStatus('Template deleted. Existing projects were not changed.', 'success');
        } catch (error) {
            if (error.status === 409) state.templates.catalog = null;
            showInlineStatus(`Template was not deleted. ${error.message}`, 'error');
            if (!state.templates.catalog) await loadTemplates({ force: true });
        }
    }

    function renderNewProjectTemplateContext() {
        const context = element('new-project-template-context');
        const name = element('new-project-template-name');
        if (context) context.hidden = !state.newProject.templateId;
        if (name) name.textContent = state.newProject.templateName || 'Template';
    }

    function clearNewProjectTemplate() {
        state.newProject.templateId = null;
        state.newProject.templateName = null;
        renderNewProjectTemplateContext();
    }

    function applyNewProjectTemplate(template) {
        if (!template) return;
        state.newProject.templateId = template.id;
        state.newProject.templateName = template.name;
        const method = document.querySelector(`input[name="new-project-method"][value="${CSS.escape(template.generation_method)}"]`);
        const preset = document.querySelector(`input[name="new-project-preset"][value="${CSS.escape(template.preset)}"]`);
        if (method) method.checked = true;
        if (preset) preset.checked = true;
        const sourceLanguage = element('new-project-source-language');
        const outputLanguage = element('new-project-output-language');
        if (sourceLanguage) sourceLanguage.value = template.source_language;
        if (outputLanguage) outputLanguage.value = template.output_language;
        const advanced = element('new-project-advanced');
        if (advanced) advanced.open = template.preset === 'custom';
        renderNewProjectTemplateContext();
        updateNewProjectAccept();
        updateNewProjectControls();
    }

    function useProjectTemplate(template) {
        window.AlexandriaNavigation?.navigate('projects', {}, { historyMode: 'push' });
        window.setTimeout(() => {
            window.dispatchEvent(new CustomEvent('alexandria:new-project-requested', {
                detail: { template },
            }));
        }, 0);
    }

    function setupTemplates() {
        element('template-search')?.addEventListener('input', event => {
            state.templates.query = event.target.value;
            syncTemplateRouteContext();
            renderTemplates();
        });
        element('template-scope-filter')?.addEventListener('change', event => {
            state.templates.scope = event.target.value;
            syncTemplateRouteContext();
            renderTemplates();
        });
        element('template-refresh')?.addEventListener('click', () => loadTemplates({ force: true }));
        element('template-loading')?.addEventListener('click', event => {
            if (event.target.closest('#template-retry')) loadTemplates({ force: true });
        });
        element('template-list')?.addEventListener('click', event => {
            const row = event.target.closest('[data-template-id]');
            if (!row) return;
            state.templates.selectedId = row.dataset.templateId;
            syncTemplateRouteContext();
            renderTemplates();
        });
        element('template-list')?.addEventListener('keydown', event => {
            const row = event.target.closest('[data-template-id]');
            const keys = ['ArrowDown', 'ArrowUp', 'Home', 'End'];
            if (!row || !keys.includes(event.key)) return;
            const rows = [...element('template-list').querySelectorAll('[data-template-id]')];
            if (!rows.length) return;
            event.preventDefault();
            const current = Math.max(0, rows.indexOf(row));
            const index = event.key === 'Home'
                ? 0
                : event.key === 'End'
                    ? rows.length - 1
                    : event.key === 'ArrowDown'
                        ? Math.min(rows.length - 1, current + 1)
                        : Math.max(0, current - 1);
            const templateId = rows[index].dataset.templateId;
            state.templates.selectedId = templateId;
            syncTemplateRouteContext();
            renderTemplates();
            window.requestAnimationFrame(() => {
                document.getElementById(`template-row-${templateId}`)?.focus({ preventScroll: true });
            });
        });
        element('template-detail')?.addEventListener('click', event => {
            const template = selectedTemplate();
            if (!template) return;
            if (event.target.closest('[data-template-use]')) useProjectTemplate(template);
            else if (event.target.closest('[data-template-edit]')) openTemplateEditor(template);
            else if (event.target.closest('[data-template-duplicate]')) duplicateTemplate(template);
            else if (event.target.closest('[data-template-default]')) setDefaultTemplate(template);
            else if (event.target.closest('[data-template-delete]')) deleteTemplate(template);
        });
        element('template-editor-method')?.addEventListener('change', updateTemplateEditorMethodRules);
        element('template-editor-form')?.addEventListener('submit', event => {
            event.preventDefault();
            saveTemplateEditor();
        });
    }

    function setCanonicalSettingsStatus(message = '', stateValue = 'info') {
        const notice = element('canonical-settings-status');
        const copy = element('canonical-settings-status-copy');
        if (!notice || !copy) return;
        notice.hidden = !message;
        notice.dataset.state = stateValue;
        copy.textContent = message;
    }

    function setCanonicalSettingsSaveState(message, stateValue) {
        const target = element('canonical-settings-save-state');
        if (!target) return;
        target.textContent = message;
        target.dataset.state = stateValue;
    }

    function applyAccessibilityPreferences(accessibility = {}) {
        document.body.dataset.settingsMotion = accessibility.motion || 'system';
        document.body.dataset.settingsContrast = accessibility.contrast || 'system';
        document.body.dataset.settingsDensity = accessibility.density || 'comfortable';
        const live = element('canonical-shell-live');
        if (live) {
            live.setAttribute(
                'aria-live',
                accessibility.status_announcements === false ? 'off' : 'polite'
            );
        }
    }

    function updateCanonicalSettingsDependentControls() {
        const keyAction = element('settings-api-key-action')?.value || 'preserve';
        const keyField = element('settings-api-key-field');
        const keyInput = element('settings-api-key');
        if (keyField) keyField.hidden = keyAction !== 'replace';
        if (keyInput) {
            keyInput.required = keyAction === 'replace';
            keyInput.disabled = keyAction !== 'replace';
            if (keyAction !== 'replace') keyInput.value = '';
        }
        const external = element('settings-speech-mode')?.value === 'external';
        const speechUrlField = element('settings-speech-url-field');
        const speechUrl = element('settings-speech-url');
        if (speechUrlField) speechUrlField.hidden = !external;
        if (speechUrl) {
            speechUrl.required = external;
            speechUrl.disabled = !external;
        }
    }

    function updateCanonicalSettingsSummary() {
        const payload = state.settings.payload;
        const template = payload?.generation_defaults?.default_template;
        const values = {
            'canonical-settings-summary-template': template?.name || 'Standard',
            'canonical-settings-summary-provider': element('settings-provider-backend')?.selectedOptions?.[0]?.textContent || 'Not set',
            'canonical-settings-summary-model': element('settings-provider-model')?.value || 'Not set',
            'canonical-settings-summary-speech': element('settings-speech-mode')?.selectedOptions?.[0]?.textContent || 'Not set',
            'canonical-settings-summary-language': element('settings-output-language')?.value || 'Not set',
            'canonical-settings-summary-storage': `${element('settings-rollback-days')?.value || '—'} days / ${element('settings-backup-gib')?.value || '—'} GiB`,
        };
        Object.entries(values).forEach(([id, value]) => {
            const target = element(id);
            if (target) target.textContent = value;
        });
    }

    function renderCanonicalSettings(payload) {
        const settings = payload?.settings || {};
        const preferences = settings.preferences || {};
        const provider = settings.provider || {};
        const speech = settings.speech || {};
        const accessibility = settings.accessibility || {};
        const storage = settings.storage || {};
        const setValue = (id, value) => {
            const target = element(id);
            if (target) target.value = value ?? '';
        };
        const setChecked = (id, value) => {
            const target = element(id);
            if (target) target.checked = value === true;
        };
        setValue('settings-source-language', preferences.default_source_language || 'English');
        setValue('settings-output-language', preferences.default_output_language || 'English');
        setChecked('settings-confirm-destructive', preferences.confirm_before_destructive !== false);
        setChecked('settings-remember-project', preferences.remember_last_project !== false);
        setValue('settings-provider-backend', provider.backend || 'auto');
        setValue('settings-provider-model', provider.model_name || '');
        setValue('settings-provider-url', provider.base_url || '');
        setValue('settings-api-key-action', 'preserve');
        setValue('settings-api-key', '');
        setValue('settings-context-length', provider.context_length || 40960);
        setValue('settings-keep-alive', provider.keep_alive ?? -1);
        setValue('settings-timeout', provider.timeout || 1800);
        setChecked('settings-thinking', provider.thinking === true);
        setChecked('settings-structured-output', true);
        setChecked('settings-corrective-retry', provider.corrective_retry !== false);
        setValue('settings-speech-mode', speech.mode || 'local');
        setValue('settings-speech-url', speech.url || 'http://127.0.0.1:7860');
        setValue('settings-speech-language', speech.language || 'Auto');
        setValue('settings-parallel-workers', speech.parallel_workers || 2);
        setValue('settings-speaker-pause', speech.pause_between_speakers_ms ?? 500);
        setValue('settings-continuation-pause', speech.pause_same_speaker_ms ?? 250);
        setValue('settings-motion', accessibility.motion || 'system');
        setValue('settings-contrast', accessibility.contrast || 'system');
        setValue('settings-density', accessibility.density || 'comfortable');
        setChecked('settings-status-announcements', accessibility.status_announcements !== false);
        setValue('settings-rollback-days', storage.rollback_retention_days || 30);
        setValue('settings-intermediate-days', storage.intermediate_retention_days || 7);
        setValue('settings-backup-gib', storage.maximum_backup_gib || 10);
        const keyState = element('settings-api-key-state');
        if (keyState) {
            keyState.textContent = provider.api_key_configured
                ? 'A key is saved. Its value is not displayed.'
                : 'No API key is saved.';
        }
        const template = payload?.generation_defaults?.default_template;
        const templateName = element('settings-default-template-name');
        const templateCopy = element('settings-default-template-copy');
        if (templateName) templateName.textContent = template?.name || 'Standard';
        if (templateCopy) {
            templateCopy.textContent = template
                ? `${template.intent} · ${templatePresetLabel(template.preset)} · ${template.output_language}`
                : 'Used when New Project begins without another selected template.';
        }
        const storageNote = element('settings-storage-enforcement');
        if (storageNote) storageNote.textContent = storage.enforcement_message || 'Cleanup is manual only.';
        updateCanonicalSettingsDependentControls();
        applyAccessibilityPreferences(accessibility);
        updateCanonicalSettingsSummary();
        state.settings.dirty = false;
        setCanonicalSettingsStatus();
        setCanonicalSettingsSaveState('Saved', 'saved');
    }

    function canonicalSettingsPayload() {
        const integer = id => Number.parseInt(element(id)?.value || '0', 10);
        const number = id => Number.parseFloat(element(id)?.value || '0');
        return {
            preferences: {
                default_source_language: element('settings-source-language')?.value.trim() || '',
                default_output_language: element('settings-output-language')?.value.trim() || '',
                confirm_before_destructive: element('settings-confirm-destructive')?.checked === true,
                remember_last_project: element('settings-remember-project')?.checked === true,
            },
            provider: {
                backend: element('settings-provider-backend')?.value || 'auto',
                base_url: element('settings-provider-url')?.value.trim() || '',
                model_name: element('settings-provider-model')?.value.trim() || '',
                api_key_mode: element('settings-api-key-action')?.value || 'preserve',
                api_key: element('settings-api-key')?.value || '',
                context_length: integer('settings-context-length'),
                keep_alive: element('settings-keep-alive')?.value.trim() || '-1',
                timeout: integer('settings-timeout'),
                thinking: element('settings-thinking')?.checked === true,
                structured_output: true,
                corrective_retry: element('settings-corrective-retry')?.checked === true,
            },
            speech: {
                mode: element('settings-speech-mode')?.value || 'local',
                url: element('settings-speech-url')?.value.trim() || '',
                language: element('settings-speech-language')?.value.trim() || '',
                parallel_workers: integer('settings-parallel-workers'),
                pause_between_speakers_ms: integer('settings-speaker-pause'),
                pause_same_speaker_ms: integer('settings-continuation-pause'),
            },
            accessibility: {
                motion: element('settings-motion')?.value || 'system',
                contrast: element('settings-contrast')?.value || 'system',
                density: element('settings-density')?.value || 'comfortable',
                status_announcements: element('settings-status-announcements')?.checked === true,
            },
            storage: {
                rollback_retention_days: integer('settings-rollback-days'),
                intermediate_retention_days: integer('settings-intermediate-days'),
                maximum_backup_gib: number('settings-backup-gib'),
                cleanup_mode: 'manual_only',
                enforcement_status: 'policy_saved_not_enforced',
                enforcement_message: element('settings-storage-enforcement')?.textContent || '',
            },
        };
    }

    async function loadSettings({ force = false } = {}) {
        const request = ++state.settings.request;
        const loading = element('settings-loading');
        const form = element('canonical-settings-form');
        const errorState = element('settings-load-error');
        if (!force && state.settings.payload) {
            renderCanonicalSettings(state.settings.payload);
            if (form) form.hidden = false;
            if (loading) loading.hidden = true;
            if (errorState) errorState.hidden = true;
            return;
        }
        if (loading) loading.hidden = false;
        if (form) form.hidden = true;
        if (errorState) errorState.hidden = true;
        try {
            const payload = await fetchJson('/api/settings');
            if (request !== state.settings.request) return;
            state.settings.payload = payload;
            renderCanonicalSettings(payload);
            if (loading) loading.hidden = true;
            if (form) form.hidden = false;
        } catch (error) {
            if (request !== state.settings.request) return;
            if (loading) loading.hidden = true;
            if (errorState) errorState.hidden = false;
            const copy = element('settings-load-error-copy');
            if (copy) copy.textContent = error.message;
        }
    }

    async function saveSettings() {
        if (state.settings.saving) return;
        const form = element('canonical-settings-form');
        if (!form?.reportValidity()) {
            setCanonicalSettingsStatus('Correct the highlighted settings before saving.', 'error');
            return;
        }
        if (!state.settings.payload?.config_fingerprint) {
            setCanonicalSettingsStatus('Settings are not loaded. Reload and try again.', 'error');
            return;
        }
        state.settings.saving = true;
        const save = element('canonical-settings-save');
        if (save) save.disabled = true;
        setCanonicalSettingsSaveState('Saving…', 'saving');
        setCanonicalSettingsStatus();
        try {
            const result = await fetchJson('/api/settings', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    expected_config_fingerprint: state.settings.payload.config_fingerprint,
                    settings: canonicalSettingsPayload(),
                }),
            });
            state.settings.payload = result;
            renderCanonicalSettings(result);
            showInlineStatus('Settings saved. Existing project artifacts were not changed.', 'success');
        } catch (error) {
            setCanonicalSettingsStatus(error.message, error.status === 409 ? 'warning' : 'error');
            setCanonicalSettingsSaveState('Not saved', 'error');
        } finally {
            state.settings.saving = false;
            if (save) save.disabled = false;
        }
    }

    function openSettingsDestination(key) {
        const route = state.settings.payload?.advanced_destinations?.[key];
        if (!route?.destination) return;
        window.AlexandriaNavigation?.navigate(route.destination, route.context || {});
    }

    function markCanonicalSettingsDirty() {
        state.settings.dirty = true;
        setCanonicalSettingsSaveState('Unsaved changes', 'dirty');
        updateCanonicalSettingsSummary();
        applyAccessibilityPreferences({
            motion: element('settings-motion')?.value || 'system',
            contrast: element('settings-contrast')?.value || 'system',
            density: element('settings-density')?.value || 'comfortable',
            status_announcements: element('settings-status-announcements')?.checked === true,
        });
    }

    function setupSettings() {
        const form = element('canonical-settings-form');
        form?.addEventListener('input', event => {
            if (event.target.id === 'settings-api-key-action') updateCanonicalSettingsDependentControls();
            markCanonicalSettingsDirty();
        });
        form?.addEventListener('change', event => {
            if (event.target.id === 'settings-api-key-action' || event.target.id === 'settings-speech-mode') {
                updateCanonicalSettingsDependentControls();
            }
            markCanonicalSettingsDirty();
        });
        form?.addEventListener('submit', event => {
            event.preventDefault();
            saveSettings();
        });
        element('settings-retry')?.addEventListener('click', () => loadSettings({ force: true }));
        element('settings-manage-templates')?.addEventListener('click', () => {
            const route = state.settings.payload?.generation_defaults?.manage_route;
            window.AlexandriaNavigation?.navigate(
                route?.destination || 'templates',
                route?.context || { return: state.route.hash }
            );
        });
        document.querySelectorAll('[data-settings-destination]').forEach(button => {
            button.addEventListener('click', () => openSettingsDestination(button.dataset.settingsDestination));
        });
        document.addEventListener('keydown', event => {
            if (state.route.destination !== 'settings') return;
            if (!(event.metaKey || event.ctrlKey) || event.key.toLocaleLowerCase() !== 's') return;
            event.preventDefault();
            saveSettings();
        });
    }

    function maintenanceStateIcon(value) {
        const normalized = String(value || '').toLocaleLowerCase();
        if (['complete', 'cached', 'ready', 'available', 'current'].includes(normalized)) {
            return 'fa-circle-check';
        }
        if (['running', 'starting'].includes(normalized)) return 'fa-spinner fa-spin';
        if (['resumable', 'new', 'incomplete', 'stale', 'warning', 'actionable'].includes(normalized)) {
            return 'fa-triangle-exclamation';
        }
        if (['blocked', 'invalid', 'missing', 'unavailable', 'failed', 'error'].includes(normalized)) {
            return 'fa-circle-exclamation';
        }
        return 'fa-circle-info';
    }

    function maintenanceRouteContext(extra = {}) {
        return {
            ...extra,
            project: extra.project
                || state.route.context.project
                || state.flow?.project?.id
                || null,
            character: extra.character
                || state.route.context.character
                || null,
            return: state.route.hash,
        };
    }

    function maintenanceLibraryContext() {
        return {
            project_id: state.route.context.project || state.flow?.project?.id || null,
            character_id: state.route.context.character || null,
            return_route: state.route.hash,
        };
    }

    function maintenanceStageRoute(stage) {
        const routes = {
            script: { destination: 'script', context: {} },
            roster: { destination: 'cast', context: {} },
            visual: { destination: 'cast', context: {} },
            persona: { destination: 'cast', context: {} },
            dataset_builder: {
                destination: 'more',
                context: { tool: 'dataset-builder', mode: 'dataset' },
            },
            audio: { destination: 'produce', context: {} },
            experimental_training: {
                destination: 'more',
                context: { tool: 'voice-training', mode: 'training' },
            },
        };
        return routes[stage?.id] || { destination: 'projects', context: {} };
    }

    function maintenanceRow({
        stateValue = 'info',
        title,
        copy = '',
        metadata = [],
        actions = '',
    }) {
        const stateLabel = titleCase(stateValue);
        const meta = [
            `<span class="maintenance-row-state">${escapeHtml(stateLabel)}</span>`,
            ...metadata.filter(Boolean).map(value => `<span>${escapeHtml(value)}</span>`),
        ].join('');
        return `
            <div class="maintenance-row">
                <span class="maintenance-row-icon" data-state="${escapeHtml(stateValue)}"><i class="fas ${maintenanceStateIcon(stateValue)}" aria-hidden="true"></i></span>
                <span class="maintenance-row-copy"><strong>${escapeHtml(title)}</strong>${copy ? `<small>${escapeHtml(copy)}</small>` : ''}</span>
                ${actions
                    ? `<span class="maintenance-row-actions">${actions}</span>`
                    : `<span class="maintenance-row-meta">${meta}</span>`}
            </div>
        `;
    }

    function maintenanceErrorRow(title, error) {
        return maintenanceRow({
            stateValue: 'error',
            title,
            copy: error?.message || String(error || 'Status unavailable.'),
        });
    }

    function renderMaintenanceSummary() {
        const recovery = state.maintenance.recovery;
        const models = state.maintenance.models;
        const library = state.maintenance.library;
        const projects = state.maintenance.projects;
        const errors = state.maintenance.errors || {};
        const recoveryBlocked = Number(recovery?.summary?.blocked || 0);
        const requiredCount = Number(models?.required_count || 0);
        const requiredProblems = Number(models?.required_missing_count || 0)
            + Number(models?.required_incomplete_count || 0);
        const requiredCached = Math.max(0, requiredCount - requiredProblems);
        const dependencies = Number(library?.summary?.referenced_count || 0);
        const invalidArtifacts = Number(library?.summary?.invalid_count || 0);
        const unavailableProjects = (projects?.projects || []).filter(
            project => !['available', 'current'].includes(project.availability_state)
        ).length;
        const errorCount = Object.keys(errors).length;
        const needsAttention = recoveryBlocked
            + requiredProblems
            + invalidArtifacts
            + unavailableProjects
            + errorCount;
        const overall = element('maintenance-overall-state');
        if (overall) overall.textContent = needsAttention ? 'Needs attention' : 'Healthy';
        const blockers = element('maintenance-recovery-blockers');
        if (blockers) blockers.textContent = recovery ? recoveryBlocked.toLocaleString() : 'Unavailable';
        const required = element('maintenance-required-models');
        if (required) {
            required.textContent = models
                ? `${requiredCached.toLocaleString()} / ${requiredCount.toLocaleString()} cached`
                : 'Unavailable';
        }
        const dependencyCount = element('maintenance-dependency-count');
        if (dependencyCount) dependencyCount.textContent = library ? dependencies.toLocaleString() : 'Unavailable';
        const trash = element('maintenance-trash-count');
        if (trash) trash.textContent = projects ? Number(projects.trash_count || 0).toLocaleString() : 'Unavailable';
    }

    function renderMaintenanceHealth() {
        const host = element('maintenance-health-list');
        if (!host) return;
        const recovery = state.maintenance.recovery;
        const error = state.maintenance.errors?.recovery;
        if (error) {
            host.innerHTML = maintenanceErrorRow('Recovery status', error);
            return;
        }
        if (!recovery) {
            host.innerHTML = '<p class="maintenance-empty">Recovery status has not been loaded.</p>';
            return;
        }
        const source = recovery.source || {};
        const sourceState = source.persisted && source.exists && source.readable !== false
            ? 'complete'
            : 'blocked';
        const sourceCopy = sourceState === 'complete'
            ? `Saved source: ${text(source.basename, 'Unnamed source')}`
            : text(source.error, 'The saved source is unavailable.');
        const rows = [maintenanceRow({
            stateValue: sourceState,
            title: 'Saved source',
            copy: sourceCopy,
            metadata: [sourceState === 'complete' ? 'Readable' : 'Unavailable'],
        })];
        (recovery.stages || []).forEach(stage => {
            const route = maintenanceStageRoute(stage);
            const progress = stage.progress || {};
            const progressCopy = Number(progress.total || 0) > 0
                ? `${Number(progress.completed || 0).toLocaleString()} of ${Number(progress.total || 0).toLocaleString()} ${text(progress.unit_label, 'items')}`
                : null;
            rows.push(maintenanceRow({
                stateValue: stage.state,
                title: stage.label,
                copy: stage.reason || stage.summary,
                actions: `
                    <span class="maintenance-row-state">${escapeHtml(titleCase(stage.state))}</span>
                    ${progressCopy ? `<span class="maintenance-refresh-state">${escapeHtml(progressCopy)}</span>` : ''}
                    <button type="button" class="btn btn-sm btn-outline-secondary" data-maintenance-destination="${escapeHtml(route.destination)}" data-maintenance-context="${escapeHtml(JSON.stringify(route.context))}">Open</button>
                `,
            }));
        });
        host.innerHTML = rows.join('');
    }

    function renderMaintenanceModels() {
        const host = element('maintenance-model-list');
        const actions = element('maintenance-model-actions');
        const summary = element('maintenance-model-summary');
        if (!host || !actions) return;
        const models = state.maintenance.models;
        const error = state.maintenance.errors?.models;
        if (error) {
            host.innerHTML = maintenanceErrorRow('Model cache', error);
            actions.replaceChildren();
            if (summary) summary.textContent = 'Unavailable';
            return;
        }
        if (!models) {
            host.innerHTML = '<p class="maintenance-empty">Model cache status has not been loaded.</p>';
            actions.replaceChildren();
            return;
        }
        const operation = models.operation || {};
        const operationRunning = Boolean(operation.running);
        if (summary) {
            summary.textContent = operationRunning
                ? `${Number(operation.completed_count || 0)} of ${Number(operation.total_count || 0)} processing`
                : `${Number(models.cached_count || 0)} cached · ${Number(models.missing_count || 0)} missing · ${Number(models.incomplete_count || 0)} incomplete`;
        }
        host.innerHTML = (models.models || []).map(item => {
            const spec = item.model || {};
            const action = item.action;
            return maintenanceRow({
                stateValue: item.state,
                title: spec.purpose || spec.repo_id || spec.key,
                copy: `${text(spec.runtime, 'Runtime unspecified')} · ${formatBytes(item.size_bytes || spec.estimated_size_bytes) || 'Size unavailable'}`,
                actions: `
                    <span class="maintenance-row-state">${escapeHtml(titleCase(item.state))}</span>
                    <span class="maintenance-refresh-state">${spec.required_by_default ? 'Required' : 'Optional'}</span>
                    ${action ? `<button type="button" class="btn btn-sm btn-outline-secondary" data-maintenance-model-action="${escapeHtml(action)}" data-maintenance-model-key="${escapeHtml(spec.key)}" ${operationRunning ? 'disabled' : ''}>Review ${escapeHtml(titleCase(action))}</button>` : ''}
                `,
            });
        }).join('') || '<p class="maintenance-empty">No registered models were reported.</p>';
        actions.innerHTML = operationRunning
            ? `<span class="maintenance-refresh-state">${escapeHtml(text(operation.message || operation.status, 'Model operation is running.'))}</span><button type="button" class="btn btn-sm btn-outline-secondary" id="maintenance-model-cancel" ${operation.cancel_requested ? 'disabled' : ''}>${operation.cancel_requested ? 'Cancelling…' : 'Cancel after current model'}</button>`
            : '<span class="maintenance-refresh-state">Downloads and repairs are never automatic.</span>';
    }

    function renderMaintenanceMemory() {
        const summary = element('maintenance-memory-summary');
        const headroom = element('maintenance-memory-headroom');
        const idle = element('maintenance-memory-idle');
        const retry = element('maintenance-memory-retry');
        const release = element('maintenance-memory-release');
        const memory = state.maintenance.memory;
        const error = state.maintenance.errors?.memory;
        if (!summary) return;
        if (error) {
            summary.textContent = `Memory status unavailable: ${error.message || error}`;
            return;
        }
        if (!memory) {
            summary.textContent = 'Memory status has not been loaded.';
            return;
        }
        const available = formatBytes(memory.memory?.available_bytes) || 'Unknown';
        const loaded = (memory.loaded_model_keys || []).length;
        summary.textContent = `${available} available · ${loaded} loaded model${loaded === 1 ? '' : 's'} · ${Number(memory.active_jobs || 0)} active job${Number(memory.active_jobs || 0) === 1 ? '' : 's'}`;
        if (headroom) headroom.value = String(memory.policy?.minimum_headroom_bytes ?? 536870912);
        if (idle) idle.value = String(memory.policy?.idle_unload_seconds ?? 900);
        if (retry) retry.checked = memory.policy?.release_and_retry_on_oom !== false;
        if (release) release.disabled = Number(memory.active_jobs || 0) > 0 || loaded === 0;
    }

    function maintenanceArtifactPriority(artifact) {
        const actionable = artifact.delete?.supported && !artifact.delete?.blocked;
        return Number(artifact.blocking_dependency_count || 0) * 1000
            + Number(artifact.dependency_count || 0) * 100
            + (actionable ? 500 : 0)
            + (artifact.state === 'invalid' ? 20 : 0)
            + (artifact.state === 'stale' ? 10 : 0)
            + (artifact.delete?.supported ? 1 : 0);
    }

    function renderMaintenanceDependencies() {
        const libraryHost = element('maintenance-library-list');
        const projectHost = element('maintenance-project-list');
        if (!libraryHost || !projectHost) return;
        const libraryError = state.maintenance.errors?.library;
        const projectError = state.maintenance.errors?.projects;
        const library = state.maintenance.library;
        const projects = state.maintenance.projects;
        if (libraryError) {
            libraryHost.innerHTML = maintenanceErrorRow('Library dependencies', libraryError);
        } else if (!library) {
            libraryHost.innerHTML = '<p class="maintenance-empty">Library dependencies have not been loaded.</p>';
        } else {
            const notable = [...(library.artifacts || [])]
                .filter(artifact => (
                    Number(artifact.dependency_count || 0) > 0
                    || artifact.state !== 'available'
                    || artifact.delete?.supported
                ))
                .sort((left, right) => maintenanceArtifactPriority(right) - maintenanceArtifactPriority(left))
                .slice(0, 16);
            libraryHost.innerHTML = notable.length ? notable.map(artifact => {
                const canInspectImpact = artifact.delete?.supported === true;
                return maintenanceRow({
                    stateValue: artifact.state,
                    title: artifact.name,
                    copy: `${titleCase(artifact.kind)} · ${Number(artifact.dependency_count || 0).toLocaleString()} dependencies · ${formatBytes(artifact.size_bytes) || 'Size unavailable'}`,
                    actions: `
                        <span class="maintenance-row-state">${escapeHtml(titleCase(artifact.state))}</span>
                        ${artifact.native_route?.destination ? `<button type="button" class="btn btn-sm btn-outline-secondary" data-maintenance-artifact-open="${escapeHtml(artifact.artifact_id)}">Open</button>` : ''}
                        ${canInspectImpact ? `<button type="button" class="btn btn-sm btn-outline-secondary" data-maintenance-library-impact="${escapeHtml(artifact.artifact_id)}">Review impact</button>` : ''}
                    `,
                });
            }).join('') : '<p class="maintenance-empty">No dependency-bearing or guarded Library artifacts were found.</p>';
        }

        if (projectError) {
            projectHost.innerHTML = maintenanceErrorRow('Projects', projectError);
        } else if (!projects) {
            projectHost.innerHTML = '<p class="maintenance-empty">Project status has not been loaded.</p>';
        } else {
            projectHost.innerHTML = (projects.projects || []).map(project => {
                const canInspectImpact = !project.current && project.storage_kind === 'managed';
                const projectState = project.current
                    ? 'current'
                    : project.availability_state === 'available'
                        ? project.compatibility_state === 'current'
                            ? 'available'
                            : 'warning'
                        : project.availability_state;
                return maintenanceRow({
                    stateValue: projectState,
                    title: project.name,
                    copy: `${project.archive_state === 'archived' ? 'Archived' : 'Active'} · ${Number(project.blocker_count || 0).toLocaleString()} workflow blockers · ${titleCase(project.storage_kind)}`,
                    actions: `
                        <span class="maintenance-row-state">${project.current ? 'Current' : escapeHtml(titleCase(project.availability_state))}</span>
                        <button type="button" class="btn btn-sm btn-outline-secondary" data-maintenance-project-open="${escapeHtml(project.id)}">Open</button>
                        ${canInspectImpact ? `<button type="button" class="btn btn-sm btn-outline-secondary" data-maintenance-project-impact="${escapeHtml(project.id)}">Review impact</button>` : ''}
                    `,
                });
            }).join('') || '<p class="maintenance-empty">No projects were found.</p>';
        }
    }

    function renderMaintenanceHistory() {
        const host = element('maintenance-history-list');
        if (!host) return;
        const historyError = state.maintenance.errors?.history;
        const recovery = state.maintenance.recovery;
        const history = state.maintenance.history;
        if (historyError) {
            host.innerHTML = maintenanceErrorRow('Migration history', historyError);
            return;
        }
        const rows = [];
        (history?.operations || []).forEach(operation => {
            rows.push(maintenanceRow({
                stateValue: operation.state,
                title: operation.operation === 'rollback' ? 'Migration rollback' : 'Migration applied',
                copy: `${Number(operation.action_count || 0).toLocaleString()} actions · ${Number(operation.changed_file_count || 0).toLocaleString()} changed files · ${formatActivity(operation.at_utc)}`,
                actions: `
                    <span class="maintenance-row-state">${escapeHtml(titleCase(operation.state))}</span>
                    ${operation.rollback_available ? `<button type="button" class="btn btn-sm btn-outline-secondary" data-maintenance-rollback="${escapeHtml(operation.operation_id)}">Review rollback</button>` : ''}
                `,
            }));
        });
        (history?.invalid_records || []).forEach(record => {
            rows.push(maintenanceRow({
                stateValue: 'invalid',
                title: `Invalid history record: ${record.operation_id}`,
                copy: record.message,
            }));
        });
        (recovery?.stages || [])
            .filter(stage => (
                Number(stage.process?.line_count || 0) > 0
                || stage.progress?.last_checkpoint_at
            ))
            .forEach(stage => {
                rows.push(maintenanceRow({
                    stateValue: stage.state,
                    title: `${stage.label} checkpoint`,
                    copy: `${Number(stage.process?.line_count || 0).toLocaleString()} recorded log lines${stage.progress?.last_checkpoint_at ? ` · ${formatActivity(stage.progress.last_checkpoint_at)}` : ''}`,
                    metadata: [stage.process?.truncated ? 'Logs truncated' : 'Logs retained'],
                }));
            });
        host.innerHTML = rows.join('') || '<p class="maintenance-empty">No migration or recovery history is recorded.</p>';
    }

    function renderMaintenanceMigration() {
        const stateLabel = element('maintenance-migration-state');
        const summary = element('maintenance-migration-summary');
        const actions = element('maintenance-migration-actions');
        if (!summary || !actions) return;
        const migration = state.maintenance.migration;
        const error = state.maintenance.errors?.migration;
        if (error) {
            if (stateLabel) stateLabel.textContent = 'Unavailable';
            summary.innerHTML = maintenanceErrorRow('Migration status', error);
            actions.replaceChildren();
            return;
        }
        if (!migration) {
            if (stateLabel) stateLabel.textContent = 'Not checked';
            summary.innerHTML = '<p class="maintenance-empty">Migration status has not been loaded.</p>';
            actions.replaceChildren();
            return;
        }
        const blocked = Boolean(migration.migration_blocked);
        const required = Boolean(migration.migration_required);
        if (stateLabel) {
            stateLabel.textContent = blocked
                ? 'Blocked'
                : required
                    ? 'Review required'
                    : 'Current';
        }
        const blockers = migration.blockers || [];
        const warnings = migration.warnings || [];
        summary.innerHTML = `
            <p class="maintenance-section-copy">${escapeHtml(
                blocked
                    ? 'Migration cannot run until the reported blockers are resolved.'
                    : required
                        ? `${migration.actions?.length || 0} additive migration action${migration.actions?.length === 1 ? '' : 's'} are available for review.`
                        : 'No migration action is currently required.'
            )}</p>
            ${blockers.length ? `<ul class="maintenance-impact-list">${blockers.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul>` : ''}
            ${warnings.length ? `<details><summary>${warnings.length.toLocaleString()} warning${warnings.length === 1 ? '' : 's'}</summary><ul class="maintenance-impact-list">${warnings.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul></details>` : ''}
        `;
        actions.innerHTML = required && !blocked
            ? '<button type="button" class="btn btn-outline-secondary" id="maintenance-review-migration">Review migration</button>'
            : '<span class="maintenance-refresh-state">Migration never runs automatically.</span>';
    }

    function renderMaintenance() {
        renderMaintenanceSummary();
        renderMaintenanceHealth();
        renderMaintenanceModels();
        renderMaintenanceMemory();
        renderMaintenanceDependencies();
        renderMaintenanceHistory();
        renderMaintenanceMigration();
    }

    function maintenancePayloadLoaded() {
        return Boolean(
            state.maintenance.recovery
            || state.maintenance.models
            || state.maintenance.memory
            || state.maintenance.library
            || state.maintenance.projects
            || state.maintenance.migration
            || state.maintenance.history
        );
    }

    async function loadMaintenance({ force = false } = {}) {
        const request = ++state.maintenance.request;
        const loading = element('maintenance-loading');
        const content = element('maintenance-content');
        const errorPanel = element('maintenance-load-error');
        const errorCopy = element('maintenance-load-error-copy');
        const refreshState = element('maintenance-refresh-state');
        if (!force && maintenancePayloadLoaded()) {
            renderMaintenance();
            if (loading) loading.hidden = true;
            if (content) content.hidden = false;
            if (errorPanel) errorPanel.hidden = true;
            return;
        }
        if (loading) loading.hidden = false;
        if (content) content.hidden = true;
        if (errorPanel) errorPanel.hidden = true;
        if (refreshState) refreshState.textContent = 'Checking';
        const context = maintenanceLibraryContext();
        const params = new URLSearchParams();
        if (context.project_id) params.set('project_id', context.project_id);
        if (context.character_id) params.set('character_id', context.character_id);
        params.set('return_route', context.return_route);
        const requests = [
            ['recovery', '/api/recovery/status'],
            ['models', '/api/model_registry/status'],
            ['memory', '/api/model_registry/memory'],
            ['library', `/api/library?${params.toString()}`],
            ['projects', '/api/projects'],
            ['migration', '/api/migration/status'],
            ['history', '/api/migration/history'],
        ];
        const results = await Promise.allSettled(
            requests.map(([, url]) => fetchJson(url))
        );
        if (request !== state.maintenance.request) return;
        state.maintenance.errors = {};
        results.forEach((result, index) => {
            const key = requests[index][0];
            if (result.status === 'fulfilled') {
                state.maintenance[key] = result.value;
            } else {
                state.maintenance[key] = null;
                state.maintenance.errors[key] = result.reason;
            }
        });
        const successCount = results.filter(result => result.status === 'fulfilled').length;
        if (!successCount) {
            if (loading) loading.hidden = true;
            if (errorPanel) errorPanel.hidden = false;
            if (errorCopy) errorCopy.textContent = 'Every Maintenance status request failed. Refresh after checking the local application service.';
            if (refreshState) refreshState.textContent = 'Unavailable';
            return;
        }
        renderMaintenance();
        if (loading) loading.hidden = true;
        if (content) content.hidden = false;
        if (errorPanel) errorPanel.hidden = true;
        if (refreshState) {
            const failed = results.length - successCount;
            refreshState.textContent = failed
                ? `${successCount} of ${results.length} checked`
                : 'Checked just now';
        }
        const mode = state.route.context.mode;
        const focusTarget = state.route.context.tool === 'model-cache' || mode === 'model-cache'
            ? element('maintenance-model-section')
            : mode === 'dependencies'
                ? element('maintenance-dependencies-title')
                : mode === 'migration'
                    ? element('maintenance-migration-title')
                    : null;
        window.setTimeout(() => focusTarget?.scrollIntoView({ block: 'start' }), 0);
    }

    function maintenanceImpactDefinition(kind, impact, trigger) {
        if (kind === 'library') {
            const blockers = impact.blockers || [];
            return {
                kind,
                impact,
                trigger,
                title: `Delete ${impact.name}`,
                summary: impact.reason || (impact.safe_to_delete
                    ? 'This artifact will be removed through its existing authoritative delete route.'
                    : 'This artifact cannot be deleted safely.'),
                facts: [
                    ['Type', titleCase(impact.kind)],
                    ['State', titleCase(impact.state)],
                    ['Dependencies', blockers.length.toLocaleString()],
                    ['Recoverable', 'No'],
                ],
                items: blockers.map(blocker => `${titleCase(blocker.scope)} · ${text(blocker.source, 'Unknown dependency')}`),
                confirmText: impact.confirm_name,
                actionLabel: 'Delete artifact',
                actionable: Boolean(impact.safe_to_delete),
            };
        }
        if (kind === 'project') {
            const categories = impact.dependencies?.categories || {};
            const items = Object.entries(categories)
                .filter(([, count]) => Number(count || 0) > 0)
                .map(([key, count]) => `${titleCase(key)} · ${Number(count).toLocaleString()}`);
            return {
                kind,
                impact,
                trigger,
                title: `Delete project ${impact.project_id}`,
                summary: impact.blocking_reason || (impact.deletable
                    ? 'The archived managed project will move to Alexandria Trash and remains recoverable.'
                    : 'This project cannot be deleted yet.'),
                facts: [
                    ['Files', Number(impact.dependencies?.file_count || 0).toLocaleString()],
                    ['Size', formatBytes(impact.dependencies?.total_bytes) || '0 B'],
                    ['Recoverable', impact.recoverable_delete ? 'Yes, from Trash' : 'No'],
                ],
                items,
                confirmText: impact.project_id,
                actionLabel: 'Move project to Trash',
                actionable: Boolean(impact.deletable),
            };
        }
        if (kind === 'migration') {
            return {
                kind,
                impact,
                trigger,
                title: 'Apply migration plan',
                summary: 'Alexandria will revalidate this exact dry-run fingerprint before writing. The current plan reports no automatic text rewrite or artifact deletion.',
                facts: [
                    ['Actions', Number(impact.actions?.length || 0).toLocaleString()],
                    ['Blockers', Number(impact.blockers?.length || 0).toLocaleString()],
                    ['Text rewritten', impact.text_rewrite_planned ? 'Planned' : 'No'],
                    ['Artifacts deleted', impact.automatic_artifact_deletion_planned ? 'Planned' : 'No'],
                ],
                items: (impact.actions || []).map(action => action.description || titleCase(action.action)),
                confirmText: 'APPLY MIGRATION',
                actionLabel: 'Apply migration',
                actionable: Boolean(impact.migration_required && !impact.migration_blocked),
            };
        }
        if (kind === 'rollback') {
            return {
                kind,
                impact,
                trigger,
                title: 'Roll back migration',
                summary: 'Rollback succeeds only when every migrated file still matches the recorded post-migration hash. Conflicts fail without overwriting newer work.',
                facts: [
                    ['Operation', impact.operation_id],
                    ['Changed files', Number(impact.changed_file_count || 0).toLocaleString()],
                    ['Applied', formatActivity(impact.at_utc)],
                ],
                items: [],
                confirmText: 'ROLL BACK',
                actionLabel: 'Roll back migration',
                actionable: Boolean(impact.rollback_available),
            };
        }
        if (kind === 'model') {
            const spec = impact.model || {};
            const action = impact.action;
            return {
                kind,
                impact,
                trigger,
                title: `${titleCase(action)} local model`,
                summary: `${titleCase(action)} is explicit and may use network, disk space, and time. Alexandria will validate the pinned revision and required files before marking the model cached.`,
                facts: [
                    ['Purpose', spec.purpose || spec.repo_id],
                    ['Runtime', spec.runtime],
                    ['Estimated size', formatBytes(spec.estimated_size_bytes)],
                    ['Current state', titleCase(impact.state)],
                ],
                items: (impact.missing_required_paths || []).map(path => `Missing required file: ${path}`),
                confirmText: String(action || '').toLocaleUpperCase(),
                actionLabel: titleCase(action),
                actionable: Boolean(action && !state.maintenance.models?.operation?.running),
            };
        }
        return null;
    }

    function openMaintenanceImpact(kind, impact, trigger) {
        const definition = maintenanceImpactDefinition(kind, impact, trigger);
        const dialog = element('maintenance-impact-dialog');
        const body = element('maintenance-impact-body');
        const title = element('maintenance-impact-title');
        const field = element('maintenance-confirm-field');
        const label = element('maintenance-confirm-label');
        const input = element('maintenance-confirm-input');
        const action = element('maintenance-delete-action');
        if (!definition || !dialog || !body || !action) return;
        state.maintenance.impact = definition;
        state.maintenance.impactTrigger = trigger || document.activeElement;
        state.maintenance.impactTrigger?.focus?.({ preventScroll: true });
        if (title) title.textContent = definition.title;
        body.innerHTML = `
            <p>${escapeHtml(definition.summary)}</p>
            <dl>${definition.facts.filter(([, value]) => value !== undefined && value !== null && value !== '').map(([term, value]) => `<dt>${escapeHtml(term)}</dt><dd>${escapeHtml(value)}</dd>`).join('')}</dl>
            ${definition.items.length ? `<div><strong>Reported impact</strong><ul class="maintenance-impact-list">${definition.items.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul></div>` : ''}
        `;
        if (field) field.hidden = !definition.confirmText;
        if (label) label.textContent = definition.confirmText
            ? `Type “${definition.confirmText}” exactly to continue.`
            : '';
        if (input) input.value = '';
        action.textContent = definition.actionLabel;
        action.disabled = !definition.actionable || Boolean(definition.confirmText);
        action.dataset.maintenanceImpactKind = kind;
        dialog.showModal();
        if (definition.confirmText) input?.focus();
        else action.focus();
    }

    function closeMaintenanceImpact() {
        const dialog = element('maintenance-impact-dialog');
        if (dialog?.open) dialog.close();
    }

    async function executeMaintenanceImpact() {
        const definition = state.maintenance.impact;
        if (!definition || state.maintenance.actionRunning || !definition.actionable) return;
        const input = element('maintenance-confirm-input');
        if (definition.confirmText && input?.value !== definition.confirmText) return;
        const button = element('maintenance-delete-action');
        state.maintenance.actionRunning = true;
        if (button) {
            button.disabled = true;
            button.textContent = 'Working…';
        }
        try {
            if (definition.kind === 'library') {
                const impact = definition.impact;
                await fetchJson(`/api/library/artifacts/${encodeURIComponent(impact.artifact_id)}`, {
                    method: 'DELETE',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        ...maintenanceLibraryContext(),
                        expected_inventory_fingerprint: impact.inventory_fingerprint,
                        expected_artifact_fingerprint: impact.artifact_fingerprint,
                        confirm_name: impact.confirm_name,
                    }),
                });
                showInlineStatus(`${impact.name} was deleted through its authoritative route.`, 'success');
            } else if (definition.kind === 'project') {
                const impact = definition.impact;
                await fetchJson(`/api/projects/${encodeURIComponent(impact.project_id)}/delete`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        confirm_project_id: impact.project_id,
                        expected_catalog_fingerprint: impact.catalog_fingerprint,
                        expected_project_fingerprint: impact.project_fingerprint,
                        confirm_dependencies: true,
                    }),
                });
                showInlineStatus(`Project ${impact.project_id} moved to recoverable Trash.`, 'success');
            } else if (definition.kind === 'migration') {
                await fetchJson('/api/migration/apply', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        plan_fingerprint: definition.impact.plan_fingerprint,
                        confirm: true,
                    }),
                });
                showInlineStatus('Migration applied. A rollback record is available in Maintenance history.', 'success');
            } else if (definition.kind === 'rollback') {
                await fetchJson('/api/migration/rollback', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ operation_id: definition.impact.operation_id }),
                });
                showInlineStatus('Migration rollback completed.', 'success');
            } else if (definition.kind === 'model') {
                await fetchJson('/api/model_registry/action', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        action: definition.impact.action,
                        model_key: definition.impact.model?.key,
                    }),
                });
                showInlineStatus(`${titleCase(definition.impact.action)} started for ${definition.impact.model?.purpose || definition.impact.model?.key}.`, 'info');
            }
            closeMaintenanceImpact();
            state.maintenance.recovery = null;
            state.maintenance.models = null;
            state.maintenance.library = null;
            state.maintenance.projects = null;
            state.maintenance.migration = null;
            state.maintenance.history = null;
            await loadMaintenance({ force: true });
        } catch (error) {
            showInlineStatus(`Maintenance action failed without completing. ${error.message}`, 'error');
            const body = element('maintenance-impact-body');
            if (body) {
                body.insertAdjacentHTML('afterbegin', `<div class="canonical-notice" data-state="error" role="alert"><i class="fas fa-circle-exclamation" aria-hidden="true"></i><span>${escapeHtml(error.message)}</span></div>`);
            }
        } finally {
            state.maintenance.actionRunning = false;
            const current = state.maintenance.impact;
            if (button && current) {
                button.textContent = current.actionLabel;
                button.disabled = !current.actionable
                    || Boolean(current.confirmText && element('maintenance-confirm-input')?.value !== current.confirmText);
            }
        }
    }

    async function reviewMaintenanceLibraryImpact(artifactId, trigger) {
        try {
            const impact = await fetchJson(`/api/library/artifacts/${encodeURIComponent(artifactId)}/delete-impact`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(maintenanceLibraryContext()),
            });
            openMaintenanceImpact('library', impact, trigger);
        } catch (error) {
            showInlineStatus(`Library impact could not be loaded. ${error.message}`, 'error');
        }
    }

    async function reviewMaintenanceProjectImpact(projectId, trigger) {
        try {
            const impact = await fetchJson(`/api/projects/${encodeURIComponent(projectId)}/delete-impact`);
            openMaintenanceImpact('project', impact, trigger);
        } catch (error) {
            showInlineStatus(`Project impact could not be loaded. ${error.message}`, 'error');
        }
    }

    function setupMaintenance() {
        document.querySelectorAll('[data-maintenance-overview]').forEach(button => {
            button.addEventListener('click', () => {
                window.AlexandriaNavigation?.navigate('more', {
                    tool: 'maintenance',
                    return: state.route.context.return || '#/settings',
                });
            });
        });
        element('maintenance-advanced-generation-save')?.addEventListener('click', () => {
            element('config-form')?.requestSubmit();
        });
        element('maintenance-refresh')?.addEventListener('click', () => loadMaintenance({ force: true }));
        element('maintenance-retry')?.addEventListener('click', () => loadMaintenance({ force: true }));
        element('maintenance-content')?.addEventListener('click', event => {
            const routeButton = event.target.closest('[data-maintenance-route]');
            if (routeButton) {
                window.AlexandriaNavigation?.navigate(
                    routeButton.dataset.maintenanceRoute,
                    maintenanceRouteContext()
                );
                return;
            }
            const destination = event.target.closest('[data-maintenance-destination]');
            if (destination) {
                let context = {};
                try {
                    context = JSON.parse(destination.dataset.maintenanceContext || '{}');
                } catch (error) {
                    context = {};
                }
                window.AlexandriaNavigation?.navigate(
                    destination.dataset.maintenanceDestination,
                    maintenanceRouteContext(context)
                );
                return;
            }
            const artifactOpen = event.target.closest('[data-maintenance-artifact-open]');
            if (artifactOpen) {
                const artifact = (state.maintenance.library?.artifacts || []).find(
                    item => item.artifact_id === artifactOpen.dataset.maintenanceArtifactOpen
                );
                const route = artifact?.native_route;
                if (route?.destination) {
                    window.AlexandriaNavigation?.navigate(
                        route.destination,
                        maintenanceRouteContext(route.context || {})
                    );
                }
                return;
            }
            const projectOpen = event.target.closest('[data-maintenance-project-open]');
            if (projectOpen) {
                window.AlexandriaNavigation?.navigate('projects', {
                    project: projectOpen.dataset.maintenanceProjectOpen,
                    return: state.route.hash,
                });
                return;
            }
            const libraryImpact = event.target.closest('[data-maintenance-library-impact]');
            if (libraryImpact) {
                reviewMaintenanceLibraryImpact(
                    libraryImpact.dataset.maintenanceLibraryImpact,
                    libraryImpact
                );
                return;
            }
            const projectImpact = event.target.closest('[data-maintenance-project-impact]');
            if (projectImpact) {
                reviewMaintenanceProjectImpact(
                    projectImpact.dataset.maintenanceProjectImpact,
                    projectImpact
                );
                return;
            }
            const modelAction = event.target.closest('[data-maintenance-model-action]');
            if (modelAction) {
                const item = (state.maintenance.models?.models || []).find(
                    model => model.model?.key === modelAction.dataset.maintenanceModelKey
                );
                if (item) {
                    openMaintenanceImpact('model', {
                        ...item,
                        action: modelAction.dataset.maintenanceModelAction,
                    }, modelAction);
                }
                return;
            }
            const rollback = event.target.closest('[data-maintenance-rollback]');
            if (rollback) {
                const operation = (state.maintenance.history?.operations || []).find(
                    item => item.operation_id === rollback.dataset.maintenanceRollback
                );
                if (operation) openMaintenanceImpact('rollback', operation, rollback);
                return;
            }
            if (event.target.closest('#maintenance-review-migration')) {
                openMaintenanceImpact(
                    'migration',
                    state.maintenance.migration,
                    event.target.closest('#maintenance-review-migration')
                );
            }
        });
        element('maintenance-model-actions')?.addEventListener('click', async event => {
            const button = event.target.closest('#maintenance-model-cancel');
            if (!button) return;
            button.disabled = true;
            try {
                await fetchJson('/api/model_registry/action/cancel', { method: 'POST' });
                showInlineStatus('Model-cache cancellation requested.', 'success');
                await loadMaintenance({ force: true });
            } catch (error) {
                showInlineStatus(error.message || 'Model-cache cancellation could not be requested.', 'error');
                button.disabled = false;
            }
        });
        element('maintenance-memory-save')?.addEventListener('click', async event => {
            const button = event.currentTarget;
            button.disabled = true;
            try {
                const result = await fetchJson('/api/model_registry/memory/policy', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        minimum_headroom_bytes: Number(element('maintenance-memory-headroom')?.value || 536870912),
                        idle_unload_seconds: Number(element('maintenance-memory-idle')?.value || 900),
                        release_and_retry_on_oom: Boolean(element('maintenance-memory-retry')?.checked),
                    }),
                });
                state.maintenance.memory = {
                    ...(state.maintenance.memory || {}),
                    policy: result.policy,
                };
                showInlineStatus('Memory policy saved.', 'success');
                renderMaintenanceMemory();
            } catch (error) {
                showInlineStatus(error.message || 'Memory policy could not be saved.', 'error');
            } finally {
                button.disabled = false;
            }
        });
        element('maintenance-memory-release')?.addEventListener('click', async event => {
            const button = event.currentTarget;
            button.disabled = true;
            try {
                const result = await fetchJson('/api/model_registry/memory/release', { method: 'POST' });
                showInlineStatus(result.released ? 'Model memory released.' : 'No loaded model memory needed release.', 'success');
                await loadMaintenance({ force: true });
            } catch (error) {
                showInlineStatus(error.message || 'Model memory could not be released.', 'error');
            } finally {
                button.disabled = false;
            }
        });
        element('maintenance-confirm-input')?.addEventListener('input', event => {
            const definition = state.maintenance.impact;
            const action = element('maintenance-delete-action');
            if (!definition || !action) return;
            action.disabled = state.maintenance.actionRunning
                || !definition.actionable
                || event.target.value !== definition.confirmText;
        });
        element('maintenance-delete-action')?.addEventListener('click', executeMaintenanceImpact);
        element('maintenance-impact-dialog')?.addEventListener('close', () => {
            const trigger = state.maintenance.impactTrigger;
            const libraryImpactId = trigger?.dataset?.maintenanceLibraryImpact;
            const projectImpactId = trigger?.dataset?.maintenanceProjectImpact;
            const modelKey = trigger?.dataset?.maintenanceModelKey;
            const rollbackId = trigger?.dataset?.maintenanceRollback;
            state.maintenance.impact = null;
            state.maintenance.impactTrigger = null;
            window.setTimeout(() => {
                const restored = trigger?.isConnected
                    ? trigger
                    : libraryImpactId
                        ? document.querySelector(`[data-maintenance-library-impact="${CSS.escape(libraryImpactId)}"]`)
                        : projectImpactId
                            ? document.querySelector(`[data-maintenance-project-impact="${CSS.escape(projectImpactId)}"]`)
                            : modelKey
                                ? document.querySelector(`[data-maintenance-model-key="${CSS.escape(modelKey)}"]`)
                                : rollbackId
                                    ? document.querySelector(`[data-maintenance-rollback="${CSS.escape(rollbackId)}"]`)
                                    : null;
                restored?.focus?.({ preventScroll: true });
            }, 50);
        });
    }

    async function openMaintenanceMode(route) {
        const mode = route?.context?.mode || null;
        const definitions = {
            'llm-profiles': {
                section: 'maintenance-stage-profiles-section',
                tool: 'llm-profiles-panel',
                load: () => window.loadLLMProfiles?.({ selectedStage: window.llmProfilesSelectedStage || 'script' }),
            },
            runtime: {
                section: 'maintenance-runtime-section',
                tool: 'llm-runtime-panel',
                load: () => window.loadLLMStatus?.(),
            },
            'advanced-generation': {
                section: 'maintenance-advanced-generation-section',
                tool: 'promptSettings',
                load: () => window.loadConfig?.(),
            },
        };
        const definition = definitions[mode] || null;
        const overview = element('maintenance-content');
        document.querySelectorAll('.maintenance-specialist-section').forEach(section => {
            section.hidden = !definition || section.id !== definition.section;
            section.toggleAttribute('inert', !definition || section.id !== definition.section);
        });
        await loadMaintenance();
        if (!definition) {
            if (overview) overview.hidden = false;
            const modelSection = route?.context?.tool === 'model-cache'
                ? element('maintenance-model-section')
                : null;
            modelSection?.focus?.({ preventScroll: true });
            return;
        }
        if (overview) overview.hidden = true;
        const tool = element(definition.tool);
        if (tool && 'open' in tool) tool.open = true;
        await Promise.resolve(definition.load?.());
        const section = element(definition.section);
        section?.scrollIntoView({ block: 'start' });
        section?.querySelector('button, summary, input, select, textarea')?.focus?.({ preventScroll: true });
    }

    function applyMoreRouteContext(route) {
        state.more.query = route?.context?.search || '';
        const search = element('more-search');
        if (search && search.value !== state.more.query) search.value = state.more.query;
    }

    function syncMoreRouteContext() {
        const query = state.more.query.trim();
        const updated = window.AlexandriaNavigation?.updateContext(
            query ? { search: query } : {},
            {
                historyMode: 'replace',
                remove: query ? [] : ['search'],
            }
        );
        if (updated) state.route = updated;
    }

    function moreContextPayload() {
        return {
            project_id: state.route.context.project || state.flow?.project?.id || null,
            character_id: state.route.context.character || null,
            source: state.route.context.source || null,
            return_route: state.route.context.return || '#/more',
        };
    }

    function moreContextKey(context) {
        return JSON.stringify([
            context.project_id || '',
            context.character_id || '',
            context.source || '',
            context.return_route || '',
        ]);
    }

    function filteredMoreTools() {
        const query = state.more.query.trim().toLocaleLowerCase();
        const tools = state.more.payload?.tools || [];
        if (!query) return tools;
        return tools.filter(tool => [
            tool.title,
            tool.description,
            tool.category_label,
            tool.tool,
            tool.availability?.message,
        ].some(value => String(value || '').toLocaleLowerCase().includes(query)));
    }

    function renderMoreContext() {
        const payload = state.more.payload;
        const context = payload?.context || {};
        const banner = element('more-context-banner');
        const label = element('more-context-label');
        const copy = element('more-context-copy');
        const returnAction = element('more-return-action');
        if (!banner) return;
        const contextual = Boolean(context.project_id || context.character_id);
        const hasReturn = context.return_route && context.return_route !== '#/more';
        banner.hidden = !contextual && !hasReturn;
        if (label) label.textContent = context.label || 'Global';
        if (copy) {
            copy.textContent = context.character_id
                ? 'Specialist tools will open for the selected character and preserve the original return route.'
                : context.project_id
                    ? 'Specialist tools will open with the current project and preserve the original return route.'
                    : 'The original return route will be preserved.';
        }
        if (returnAction) {
            returnAction.hidden = !hasReturn;
            returnAction.textContent = context.character_id
                ? 'Return to character'
                : context.project_id
                    ? 'Return to project'
                    : 'Return';
        }
    }

    function renderMoreTools() {
        const payload = state.more.payload;
        const visibleTools = filteredMoreTools();
        const host = element('more-tool-groups');
        if (!host) return;
        const groups = (payload?.categories || []).map(category => ({
            ...category,
            tools: visibleTools.filter(tool => tool.category === category.id),
        })).filter(group => group.tools.length);
        host.innerHTML = groups.length ? groups.map(group => `
            <section class="more-tool-group" aria-labelledby="more-group-${escapeHtml(group.id)}">
                <div class="more-tool-group-heading">
                    <h3 id="more-group-${escapeHtml(group.id)}">${escapeHtml(group.label)}</h3>
                    <span>${group.tools.length.toLocaleString()} ${group.tools.length === 1 ? 'tool' : 'tools'}</span>
                </div>
                <div class="more-tool-list" role="list">
                    ${group.tools.map(tool => `
                        <button type="button" class="more-tool-row" role="listitem" data-more-tool-id="${escapeHtml(tool.tool_id)}" data-more-tool="${escapeHtml(tool.tool)}">
                            <span class="more-tool-icon"><i class="fas ${escapeHtml(tool.icon)}" aria-hidden="true"></i></span>
                            <span class="more-tool-copy">
                                <strong>${escapeHtml(tool.title)}</strong>
                                <small>${escapeHtml(tool.description)}</small>
                                <span class="more-tool-context">${escapeHtml(tool.availability?.message || '')}</span>
                            </span>
                            <span class="more-tool-state" data-state="${escapeHtml(tool.danger_level)}">${escapeHtml(titleCase(tool.danger_level))}</span>
                            <i class="fas fa-chevron-right" aria-hidden="true"></i>
                        </button>
                    `).join('')}
                </div>
            </section>
        `).join('') : `
            <div class="canonical-empty-state">
                <span class="canonical-empty-mark" aria-hidden="true"><i class="fas fa-filter-circle-xmark"></i></span>
                <div><strong>No specialist tool matches</strong><p>Clear the search to restore all More tools.</p></div>
            </div>
        `;
        const count = element('more-result-count');
        if (count) {
            count.textContent = `${visibleTools.length.toLocaleString()} of ${Number(payload?.summary?.tool_count || 0).toLocaleString()}`;
        }
        renderMoreContext();
    }

    async function loadMoreTools({ force = false } = {}) {
        const context = moreContextPayload();
        const contextKey = moreContextKey(context);
        const request = ++state.more.request;
        const loading = element('more-loading');
        const content = element('more-content');
        if (!force && state.more.payload && state.more.contextKey === contextKey) {
            renderMoreTools();
            if (loading) loading.hidden = true;
            if (content) content.hidden = false;
            return;
        }
        if (loading) loading.hidden = false;
        if (content) content.hidden = true;
        try {
            const params = new URLSearchParams();
            if (context.project_id) params.set('project_id', context.project_id);
            if (context.character_id) params.set('character_id', context.character_id);
            if (context.source) params.set('source', context.source);
            params.set('return_route', context.return_route);
            const payload = await fetchJson(`/api/more?${params.toString()}`);
            if (request !== state.more.request) return;
            state.more.payload = payload;
            state.more.contextKey = contextKey;
            renderMoreTools();
            if (loading) loading.hidden = true;
            if (content) content.hidden = false;
        } catch (error) {
            if (request !== state.more.request) return;
            if (loading) loading.hidden = true;
            if (content) {
                content.hidden = false;
                content.innerHTML = `
                    <div class="canonical-error-state" role="alert">
                        <div><strong>More could not be loaded</strong><p>${escapeHtml(error.message)}</p></div>
                        <button type="button" class="btn btn-outline-secondary" id="more-retry">Retry</button>
                    </div>
                `;
            }
        }
    }

    function openMoreTool(toolId) {
        const tool = (state.more.payload?.tools || []).find(item => item.tool_id === toolId);
        const route = tool?.route;
        if (!route?.destination) return;
        window.AlexandriaNavigation?.navigate(route.destination, route.context || {});
    }

    function returnFromMoreLanding() {
        const hash = state.more.payload?.context?.return_route;
        if (!hash || hash === '#/more') return;
        const route = routeApi.parseHash(hash);
        window.AlexandriaNavigation?.navigate(route.destination, route.context || {});
    }

    function setupMore() {
        element('more-search')?.addEventListener('input', event => {
            state.more.query = event.target.value;
            syncMoreRouteContext();
            renderMoreTools();
        });
        element('more-tool-groups')?.addEventListener('click', event => {
            const row = event.target.closest('[data-more-tool-id]');
            if (row) openMoreTool(row.dataset.moreToolId);
        });
        element('more-return-action')?.addEventListener('click', returnFromMoreLanding);
        element('more-loading')?.addEventListener('click', event => {
            if (event.target.closest('#more-retry')) loadMoreTools({ force: true });
        });
    }

    function helpContextIdForRoute(routeValue) {
        const route = routeApi.normalizeRoute(routeValue || state.route);
        if (route.destination === 'more') {
            const toolContexts = {
                'advanced-character-operations': 'character-identity',
                'voice-designer': 'voice-library',
                'audio-preparer': 'voice-library',
                'dataset-builder': 'voice-library',
                'voice-training': 'voice-library',
                maintenance: 'maintenance',
                'model-cache': 'model-cache',
            };
            return toolContexts[route.context.tool] || null;
        }
        return {
            projects: 'projects',
            script: 'script',
            cast: 'cast',
            produce: 'produce',
            export: 'export',
            library: 'library',
            voices: 'voices',
            templates: 'templates',
            settings: 'settings',
        }[route.destination] || null;
    }

    function updateContextualHelpActions(routeValue) {
        const route = routeApi.normalizeRoute(routeValue || state.route);
        const contextId = helpContextIdForRoute(route);
        for (const id of ('global-help-action project-help-action').split(' ')) {
            const button = element(id);
            if (!button) continue;
            button.hidden = !contextId;
            button.dataset.helpContext = contextId || '';
            button.title = contextId
                ? `Help for ${globalCopyForRoute(route).title}`
                : 'Help is already open';
            button.setAttribute('aria-label', button.title);
        }
    }

    function openContextualHelp(contextId) {
        const safeContext = String(contextId || '').trim();
        if (!safeContext) return;
        const current = routeApi.normalizeRoute(state.route);
        const context = {
            ...current.context,
            tool: 'help-center',
            help: safeContext,
            return: current.hash,
        };
        delete context.topic;
        delete context.search;
        delete context.filter;
        window.AlexandriaNavigation?.navigate('more', context);
    }

    function applyHelpRouteContext(routeValue) {
        const route = routeApi.normalizeRoute(routeValue || state.route);
        state.help.query = route.context.search || '';
        const search = element('help-search');
        if (search && search.value !== state.help.query) search.value = state.help.query;
        const requestedTopic = route.context.topic;
        const contextualTopic = route.context.help
            ? state.help.contextIndex[route.context.help]
            : null;
        if (requestedTopic && state.help.topics.some(topic => topic.slug === requestedTopic)) {
            state.help.selectedSlug = requestedTopic;
        } else if (contextualTopic && state.help.topics.some(topic => topic.slug === contextualTopic)) {
            state.help.selectedSlug = contextualTopic;
        }
        const returnAction = element('help-return-more');
        if (returnAction) {
            returnAction.textContent = route.context.return ? 'Return' : 'All tools';
            returnAction.title = route.context.return
                ? 'Return to the page that opened Help'
                : 'Return to More';
        }
    }

    function syncHelpRouteContext(changes = {}, options = {}) {
        const remove = Array.isArray(options.remove)
            ? options.remove
            : [];
        const updated = window.AlexandriaNavigation?.updateContext(
            changes,
            {
                historyMode: options.historyMode || 'replace',
                remove,
            }
        );
        if (updated) state.route = updated;
        return updated;
    }

    function returnFromHelpCenter() {
        const returnHash = state.route.context.return;
        if (returnHash) {
            const route = routeApi.parseHash(returnHash);
            window.AlexandriaNavigation?.navigate(
                route.destination,
                route.context,
                { historyMode: 'push' }
            );
            return;
        }
        window.AlexandriaNavigation?.navigate('more', {
            project: state.route.context.project || null,
            character: state.route.context.character || null,
        });
    }

    function appendHelpInlineText(target, value) {
        const parts = String(value || '').split(/(`[^`]+`)/g);
        parts.forEach(part => {
            if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {
                const code = document.createElement('code');
                code.textContent = part.slice(1, -1);
                target.append(code);
            } else if (part) {
                target.append(document.createTextNode(part));
            }
        });
    }

    function renderHelpMarkdown(target, markdown, title) {
        target.replaceChildren();
        const lines = String(markdown || '').replace(/\r\n?/g, '\n').split('\n');
        let list = null;
        let codeBlock = null;
        let skippedTitle = false;
        const closeList = () => { list = null; };
        lines.forEach(rawLine => {
            const line = rawLine.trimEnd();
            if (line.startsWith('```')) {
                closeList();
                if (codeBlock) {
                    codeBlock = null;
                } else {
                    const pre = document.createElement('pre');
                    const code = document.createElement('code');
                    pre.append(code);
                    target.append(pre);
                    codeBlock = code;
                }
                return;
            }
            if (codeBlock) {
                codeBlock.append(document.createTextNode(`${rawLine}\n`));
                return;
            }
            if (!line.trim()) {
                closeList();
                return;
            }
            const heading = line.match(/^(#{1,4})\s+(.+)$/);
            if (heading) {
                closeList();
                const headingText = heading[2].trim();
                if (!skippedTitle && heading[1].length === 1 && headingText === title) {
                    skippedTitle = true;
                    return;
                }
                const node = document.createElement(`h${Math.min(4, heading[1].length + 1)}`);
                appendHelpInlineText(node, headingText);
                target.append(node);
                return;
            }
            const unordered = line.match(/^[-*]\s+(.+)$/);
            const ordered = line.match(/^\d+[.)]\s+(.+)$/);
            if (unordered || ordered) {
                const tagName = ordered ? 'ol' : 'ul';
                if (!list || list.tagName.toLocaleLowerCase() !== tagName) {
                    list = document.createElement(tagName);
                    target.append(list);
                }
                const item = document.createElement('li');
                appendHelpInlineText(item, (unordered || ordered)[1]);
                list.append(item);
                return;
            }
            closeList();
            const paragraph = document.createElement('p');
            appendHelpInlineText(paragraph, line.trim());
            target.append(paragraph);
        });
    }

    function filteredHelpTopics() {
        return state.help.topics;
    }

    function renderHelpTopicList() {
        const topics = filteredHelpTopics();
        if (topics.length && !topics.some(topic => topic.slug === state.help.selectedSlug)) {
            state.help.selectedSlug = topics[0].slug;
        }
        if (!topics.length) state.help.selectedSlug = null;
        const list = element('help-topic-list');
        if (list) {
            list.innerHTML = topics.length
                ? topics.map(topic => {
                    const selected = topic.slug === state.help.selectedSlug;
                    return `
                        <button type="button" class="supporting-list-row" id="help-topic-${escapeHtml(topic.slug)}" role="option" aria-selected="${selected}" tabindex="${selected ? '0' : '-1'}" data-help-topic="${escapeHtml(topic.slug)}">
                            <span class="supporting-list-icon"><i class="far fa-file-lines" aria-hidden="true"></i></span>
                            <span class="supporting-list-copy"><strong>${escapeHtml(topic.title)}</strong><small>${escapeHtml(topic.summary)}</small></span>
                            <i class="fas fa-chevron-right" aria-hidden="true"></i>
                        </button>
                    `;
                }).join('')
                : '<div class="canonical-empty-state"><div><strong>No help topic matches</strong><p>Clear the search to restore all bundled guidance.</p></div></div>';
            if (state.help.selectedSlug) {
                list.setAttribute(
                    'aria-activedescendant',
                    `help-topic-${state.help.selectedSlug}`
                );
            } else {
                list.removeAttribute('aria-activedescendant');
            }
        }
        const count = element('help-result-count');
        if (count) {
            count.textContent = `${topics.length.toLocaleString()} of ${Number(state.help.totalCount || topics.length).toLocaleString()}`;
        }
    }

    function helpDestinationContext(destination) {
        const [routeDestination, tool] = String(destination || '').split(':', 2);
        const context = {
            ...state.route.context,
            return: state.route.hash,
        };
        delete context.help;
        delete context.topic;
        delete context.search;
        delete context.filter;
        delete context.tool;
        if (tool) context.tool = tool;
        return {
            destination: routeDestination || 'more',
            context,
        };
    }

    function helpDestinationLabel(destination) {
        const [routeDestination, tool] = String(destination || '').split(':', 2);
        if (tool) return MORE_TOOL_COPY[tool]?.title || titleCase(tool);
        return GLOBAL_COPY[routeDestination]?.title
            || STAGE_LABELS[routeDestination]
            || titleCase(routeDestination);
    }

    async function loadHelpTopic(slug) {
        if (!slug) return;
        try {
            const topic = await fetchJson(`/api/help/${encodeURIComponent(slug)}`);
            if (slug !== state.help.selectedSlug) return;
            const detail = element('help-topic-detail');
            if (!detail) return;
            detail.replaceChildren();
            const header = document.createElement('header');
            header.className = 'supporting-detail-header';
            const heading = document.createElement('div');
            const kicker = document.createElement('span');
            kicker.className = 'canonical-kicker';
            kicker.textContent = 'Offline Help Center';
            const title = document.createElement('h2');
            title.textContent = topic.title;
            const metadata = document.createElement('p');
            metadata.className = 'help-topic-meta';
            metadata.textContent = `${topic.summary} · Topic ${topic.version} · Bundle ${topic.bundle_version || state.help.bundleVersion || 'unknown'}`;
            heading.append(kicker, title, metadata);
            header.append(heading);
            detail.append(header);
            const article = document.createElement('div');
            article.className = 'help-article-body';
            renderHelpMarkdown(article, topic.markdown, topic.title);
            detail.append(article);

            if (topic.destinations?.length) {
                const destinationSection = document.createElement('section');
                destinationSection.className = 'supporting-section help-context-link';
                const sectionTitle = document.createElement('h3');
                sectionTitle.textContent = 'Open this workflow';
                const actions = document.createElement('div');
                actions.className = 'supporting-detail-actions';
                topic.destinations.forEach(destination => {
                    const route = helpDestinationContext(destination);
                    const button = document.createElement('button');
                    button.type = 'button';
                    button.className = 'btn btn-outline-secondary';
                    button.textContent = helpDestinationLabel(destination);
                    button.addEventListener('click', () => {
                        window.AlexandriaNavigation?.navigate(route.destination, route.context);
                    });
                    actions.append(button);
                });
                destinationSection.append(sectionTitle, actions);
                detail.append(destinationSection);
            }

            if (topic.related_topics?.length) {
                const relatedSection = document.createElement('section');
                relatedSection.className = 'supporting-section';
                const sectionTitle = document.createElement('h3');
                sectionTitle.textContent = 'Related topics';
                const relatedList = document.createElement('ul');
                relatedList.className = 'help-related-list';
                topic.related_topics.forEach(related => {
                    const item = document.createElement('li');
                    const button = document.createElement('button');
                    button.type = 'button';
                    button.className = 'btn btn-link p-0 text-start';
                    button.textContent = related.title;
                    button.addEventListener('click', () => selectHelpTopic(related.slug));
                    const summary = document.createElement('small');
                    summary.textContent = related.summary;
                    item.append(button, summary);
                    relatedList.append(item);
                });
                relatedSection.append(sectionTitle, relatedList);
                detail.append(relatedSection);
            }
        } catch (error) {
            const detail = element('help-topic-detail');
            if (detail) {
                detail.innerHTML = `<div class="canonical-error-state" role="alert"><div><strong>Help topic could not be loaded</strong><p>${escapeHtml(error.message)}</p></div></div>`;
            }
        }
    }

    function selectHelpTopic(
        slug,
        { updateRoute = true, focusRow = false, historyMode = 'push' } = {}
    ) {
        if (!state.help.topics.some(topic => topic.slug === slug)) return;
        state.help.selectedSlug = slug;
        renderHelpTopicList();
        loadHelpTopic(slug);
        if (updateRoute && state.route.context.topic !== slug) {
            syncHelpRouteContext(
                { topic: slug },
                { historyMode }
            );
        }
        if (focusRow) {
            window.setTimeout(() => {
                document.querySelector(
                    `[data-help-topic="${CSS.escape(slug)}"]`
                )?.focus({ preventScroll: true });
            }, 0);
        }
    }

    async function loadHelpCenter({ force = false } = {}) {
        const request = ++state.help.request;
        const loading = element('help-loading');
        const content = element('help-content');
        applyHelpRouteContext(state.route);
        const query = state.help.query.trim();
        if (
            !force
            && state.help.loadedQuery === query
            && state.help.topics.length
        ) {
            applyHelpRouteContext(state.route);
            renderHelpTopicList();
            await loadHelpTopic(state.help.selectedSlug);
            if (loading) loading.hidden = true;
            if (content) content.hidden = false;
            return;
        }
        if (loading) {
            loading.hidden = false;
            loading.innerHTML = '<span class="spinner-border spinner-border-sm" aria-hidden="true"></span><span>Searching bundled guidance…</span>';
        }
        if (content) content.hidden = true;
        try {
            const params = new URLSearchParams();
            if (query) params.set('search', query);
            const inventory = await fetchJson(
                `/api/help${params.toString() ? `?${params.toString()}` : ''}`
            );
            if (request !== state.help.request) return;
            state.help.topics = inventory.topics || [];
            state.help.totalCount = Number(
                inventory.summary?.topic_count || state.help.topics.length
            );
            state.help.contextIndex = inventory.context_index || {};
            state.help.bundleVersion = inventory.bundle_version || null;
            state.help.loadedQuery = query;
            applyHelpRouteContext(state.route);
            if (
                !state.help.selectedSlug
                || !state.help.topics.some(
                    topic => topic.slug === state.help.selectedSlug
                )
            ) {
                state.help.selectedSlug = state.help.topics[0]?.slug || null;
            }
            renderHelpTopicList();
            if (state.help.selectedSlug) {
                await loadHelpTopic(state.help.selectedSlug);
            } else {
                const detail = element('help-topic-detail');
                if (detail) {
                    detail.innerHTML = '<div class="canonical-empty-state"><div><strong>No help topic matches</strong><p>Try different words or clear the search to restore all bundled guidance.</p></div></div>';
                }
            }
            if (loading) loading.hidden = true;
            if (content) content.hidden = false;
        } catch (error) {
            if (request !== state.help.request) return;
            if (loading) {
                loading.hidden = false;
                loading.innerHTML = `<div class="canonical-error-state" role="alert"><div><strong>Help Center could not be loaded</strong><p>${escapeHtml(error.message)}</p></div><button type="button" class="btn btn-outline-secondary" id="help-retry">Retry</button></div>`;
            }
        }
    }

    function scheduleHelpSearch() {
        if (state.help.searchTimer) {
            window.clearTimeout(state.help.searchTimer);
        }
        state.help.searchTimer = window.setTimeout(() => {
            state.help.searchTimer = null;
            loadHelpCenter({ force: true });
        }, 140);
    }

    function setupSupportingDestinations() {
        element('library-search')?.addEventListener('input', event => {
            state.library.query = event.target.value;
            syncLibraryRouteContext();
            renderLibrary();
        });
        element('library-kind-filter')?.addEventListener('change', event => {
            state.library.kind = event.target.value;
            syncLibraryRouteContext();
            renderLibrary();
        });
        element('library-state-filter')?.addEventListener('change', event => {
            state.library.stateFilter = event.target.value;
            syncLibraryRouteContext();
            renderLibrary();
        });
        element('library-refresh')?.addEventListener('click', () => {
            state.library.inventory = null;
            if (state.route.destination === 'voices') loadVoices({ force: true });
            else loadLibrary({ force: true });
        });
        element('library-loading')?.addEventListener('click', event => {
            if (!event.target.closest('#library-retry, [data-library-retry]')) return;
            if (state.route.destination === 'voices') loadVoices({ force: true });
            else loadLibrary({ force: true });
        });
        element('library-artifact-list')?.addEventListener('click', event => {
            const row = event.target.closest('[data-library-artifact]');
            if (!row) return;
            state.library.selectedId = row.dataset.libraryArtifact;
            renderLibrary();
        });
        element('library-artifact-list')?.addEventListener('keydown', event => {
            const row = event.target.closest('[data-library-artifact]');
            const keys = ['ArrowDown', 'ArrowUp', 'Home', 'End'];
            if (!row || !keys.includes(event.key)) return;
            const rows = [...element('library-artifact-list').querySelectorAll('[data-library-artifact]')];
            if (!rows.length) return;
            event.preventDefault();
            const current = Math.max(0, rows.indexOf(row));
            const index = event.key === 'Home'
                ? 0
                : event.key === 'End'
                    ? rows.length - 1
                    : event.key === 'ArrowDown'
                        ? Math.min(rows.length - 1, current + 1)
                        : Math.max(0, current - 1);
            const artifactId = rows[index].dataset.libraryArtifact;
            state.library.selectedId = artifactId;
            renderLibrary();
            window.requestAnimationFrame(() => {
                document.getElementById(`library-artifact-${artifactId}`)?.focus({ preventScroll: true });
            });
        });
        element('library-artifact-detail')?.addEventListener('click', event => {
            const preview = event.target.closest('[data-voice-preview]');
            if (preview) {
                const artifact = (state.library.inventory?.artifacts || []).find(
                    item => item.artifact_id === preview.dataset.voicePreview
                );
                if (artifact) setPersistentVoicePreview(artifact);
                return;
            }
            const castCharacter = event.target.closest('[data-voice-cast-character]');
            if (castCharacter) {
                window.AlexandriaNavigation?.navigate('cast', {
                    project: state.route.context.project || state.flow?.project?.id || null,
                    character: castCharacter.dataset.voiceCastCharacter || null,
                    source: 'voice-library',
                    return: state.route.hash,
                });
                return;
            }
            const cast = event.target.closest('[data-voice-cast]');
            if (cast) {
                const artifact = (state.library.inventory?.artifacts || []).find(
                    item => item.artifact_id === cast.dataset.voiceCast
                );
                const route = artifact?.assignment_route;
                if (route?.destination) {
                    window.AlexandriaNavigation?.navigate(route.destination, {
                        ...(route.context || {}),
                        return: state.route.hash,
                    });
                }
                return;
            }
            const open = event.target.closest('[data-library-open]');
            if (open) openLibraryArtifact(open.dataset.libraryOpen);
            const remove = event.target.closest('[data-library-delete]');
            if (remove) deleteLibraryArtifact(remove.dataset.libraryDelete);
        });
        for (const id of ['global-help-action', 'project-help-action']) {
            element(id)?.addEventListener('click', event => {
                openContextualHelp(event.currentTarget.dataset.helpContext);
            });
        }
        element('help-search')?.addEventListener('input', event => {
            state.help.query = event.target.value;
            syncHelpRouteContext(
                state.help.query.trim()
                    ? { search: state.help.query.trim() }
                    : {},
                {
                    historyMode: 'replace',
                    remove: state.help.query.trim() ? [] : ['search'],
                }
            );
            scheduleHelpSearch();
        });
        element('help-topic-list')?.addEventListener('click', event => {
            const row = event.target.closest('[data-help-topic]');
            if (row) selectHelpTopic(row.dataset.helpTopic);
        });
        element('help-topic-list')?.addEventListener('keydown', event => {
            const row = event.target.closest('[data-help-topic]');
            if (!row) return;
            const rows = [...event.currentTarget.querySelectorAll('[data-help-topic]')];
            const currentIndex = rows.indexOf(row);
            let nextIndex = currentIndex;
            if (event.key === 'ArrowDown') nextIndex = Math.min(rows.length - 1, currentIndex + 1);
            else if (event.key === 'ArrowUp') nextIndex = Math.max(0, currentIndex - 1);
            else if (event.key === 'Home') nextIndex = 0;
            else if (event.key === 'End') nextIndex = rows.length - 1;
            else return;
            event.preventDefault();
            const next = rows[nextIndex];
            if (next) {
                selectHelpTopic(next.dataset.helpTopic, {
                    focusRow: true,
                    historyMode: 'replace',
                });
            }
        });
        element('help-loading')?.addEventListener('click', event => {
            if (event.target.closest('#help-retry')) loadHelpCenter({ force: true });
        });
        element('help-return-more')?.addEventListener('click', returnFromHelpCenter);
    }

    function setupPersistentPlayer() {
        const host = element('persistent-player-host');
        const audio = element('main-audio');
        const slot = element('persistent-player-audio-slot');
        const play = element('persistent-player-play');
        const back = element('persistent-player-back');
        const forward = element('persistent-player-forward');
        const timeline = element('persistent-player-timeline');
        const elapsed = element('persistent-player-elapsed');
        const duration = element('persistent-player-duration');
        const volume = element('persistent-player-volume');
        const speed = element('persistent-player-speed');
        if (!host || !audio || !slot || !play || !timeline) return;

        slot.appendChild(audio);
        audio.removeAttribute('controls');
        audio.tabIndex = -1;
        audio.setAttribute('aria-hidden', 'true');

        const formatTime = secondsValue => {
            const seconds = Number.isFinite(secondsValue) ? Math.max(0, Math.floor(secondsValue)) : 0;
            const minutes = Math.floor(seconds / 60);
            return `${minutes}:${String(seconds % 60).padStart(2, '0')}`;
        };
        const update = () => {
            const hasSource = Boolean(audio.currentSrc || audio.querySelector('source')?.getAttribute('src'));
            host.hidden = false;
            document.body.classList.add('has-active-player');
            play.disabled = !hasSource;
            back.disabled = !hasSource;
            forward.disabled = !hasSource;
            timeline.disabled = !hasSource;
            const total = Number.isFinite(audio.duration) ? audio.duration : 0;
            const current = Number.isFinite(audio.currentTime) ? audio.currentTime : 0;
            timeline.max = String(Math.max(1, Math.round(total)));
            timeline.value = String(Math.min(Number(timeline.max), Math.round(current)));
            if (elapsed) elapsed.textContent = formatTime(current);
            if (duration) duration.textContent = formatTime(total);
            play.innerHTML = `<i class="fas ${audio.paused ? 'fa-play' : 'fa-pause'}" aria-hidden="true"></i>`;
            play.setAttribute('aria-label', audio.paused ? 'Play audiobook' : 'Pause audiobook');
            play.title = audio.paused ? 'Play' : 'Pause';
        };

        play.addEventListener('click', () => {
            if (audio.paused) audio.play().catch(() => {});
            else audio.pause();
        });
        back.addEventListener('click', () => { audio.currentTime = Math.max(0, audio.currentTime - 10); });
        forward.addEventListener('click', () => {
            audio.currentTime = Math.min(Number.isFinite(audio.duration) ? audio.duration : audio.currentTime + 10, audio.currentTime + 10);
        });
        timeline.addEventListener('input', () => { audio.currentTime = Number(timeline.value); });
        volume?.addEventListener('input', () => { audio.volume = Number(volume.value); });
        speed?.addEventListener('change', () => { audio.playbackRate = Number(speed.value); });
        ['loadedmetadata', 'durationchange', 'timeupdate', 'play', 'pause', 'ended', 'emptied'].forEach(type => audio.addEventListener(type, update));
        const source = audio.querySelector('source');
        if (source) {
            new MutationObserver(update).observe(source, { attributes: true, attributeFilter: ['src'] });
        }
        update();
    }

    function newProjectMethod() {
        return document.querySelector('input[name="new-project-method"]:checked')?.value || 'local';
    }

    function newProjectPreset() {
        return document.querySelector('input[name="new-project-preset"]:checked')?.value || 'standard';
    }

    function normalizedLanguage(value) {
        const normalized = String(value || '').trim();
        const known = {
            en: 'English',
            eng: 'English',
            sv: 'Swedish',
            swe: 'Swedish',
            de: 'German',
            deu: 'German',
            ger: 'German',
            fr: 'French',
            fra: 'French',
            fre: 'French',
            es: 'Spanish',
            spa: 'Spanish',
            it: 'Italian',
            ita: 'Italian',
        };
        return known[normalized.toLocaleLowerCase()] || normalized || 'English';
    }

    function setNewProjectStatus(message, status = 'info') {
        const notice = element('new-project-status');
        const copy = element('new-project-status-copy');
        if (!notice || !copy) return;
        notice.hidden = !message;
        if (!message) {
            copy.textContent = '';
            delete notice.dataset.state;
            return;
        }
        notice.dataset.state = status;
        copy.textContent = message;
        const icon = notice.querySelector('i');
        if (icon) {
            icon.className = `fas ${status === 'success'
                ? 'fa-circle-check'
                : status === 'error'
                    ? 'fa-circle-exclamation'
                    : status === 'warning'
                        ? 'fa-triangle-exclamation'
                        : 'fa-circle-info'}`;
        }
    }

    function updateNewProjectAccept() {
        const source = element('new-project-source');
        if (!source) return;
        source.accept = newProjectMethod() === 'import_existing_script'
            ? '.json,application/json'
            : '.epub,.txt,application/epub+zip,text/plain';
        const help = element('new-project-source-help');
        if (help) {
            help.textContent = newProjectMethod() === 'import_existing_script'
                ? 'Choose a completed Alexandria Script JSON file. It is validated before import.'
                : 'Choose an EPUB or UTF-8 text file. It is inspected before attachment.';
        }
    }

    function updateNewProjectControls() {
        const project = state.newProject;
        const method = newProjectMethod();
        const requiredValues = [
            element('new-project-name')?.value,
            element('new-project-title')?.value,
            element('new-project-source-language')?.value,
            element('new-project-output-language')?.value,
        ].every(value => String(value || '').trim());
        const sourceValid = Boolean(
            project.sourceFile
            && project.inspection?.valid
            && project.inspection?.generation_method === method
        );
        const submit = element('new-project-submit');
        if (submit) {
            submit.disabled = project.creating
                || project.inspecting
                || (!project.completed && (!sourceValid || !requiredValues));
            submit.textContent = project.completed
                ? 'Done'
                : project.creating
                    ? 'Creating project…'
                    : 'Create Project';
        }
        const footer = element('new-project-footer-state');
        if (footer) {
            if (project.completed) {
                footer.textContent = 'Project created safely.';
            } else if (project.creating) {
                footer.textContent = 'Writing the managed project transaction…';
            } else if (project.inspecting) {
                footer.textContent = 'Validating the selected source…';
            } else if (!sourceValid) {
                footer.textContent = 'Choose a valid source to continue.';
            } else if (!requiredValues) {
                footer.textContent = 'Complete the required identity and language fields.';
            } else {
                footer.textContent = 'Ready to create.';
            }
        }
        const inspecting = element('new-project-inspecting');
        if (inspecting) inspecting.hidden = !project.inspecting;
        const dismissButtons = document.querySelectorAll('#newProjectModal [data-bs-dismiss="modal"]');
        dismissButtons.forEach(button => { button.disabled = project.creating; });
    }

    function resetNewProjectForm() {
        element('new-project-form')?.reset();
        Object.assign(state.newProject, {
            sourceFile: null,
            inspection: null,
            templateId: null,
            templateName: null,
            inspectionRequest: state.newProject.inspectionRequest + 1,
            inspecting: false,
            creating: false,
            completed: false,
            createdProject: null,
        });
        element('new-project-source-summary')?.setAttribute('hidden', '');
        const fileName = element('new-project-file-name');
        const fileMeta = element('new-project-file-meta');
        const fileAction = element('new-project-file-action');
        if (fileName) fileName.textContent = 'Choose source file';
        if (fileMeta) fileMeta.textContent = 'EPUB, UTF-8 text, or Alexandria Script JSON';
        if (fileAction) fileAction.textContent = 'Choose';
        const cover = element('new-project-cover');
        const coverImage = element('new-project-cover-image');
        if (cover) delete cover.dataset.hasCover;
        if (coverImage) {
            coverImage.hidden = true;
            coverImage.removeAttribute('src');
        }
        const advanced = element('new-project-advanced');
        if (advanced) advanced.open = false;
        renderNewProjectTemplateContext();
        setNewProjectStatus('', 'info');
        updateNewProjectAccept();
        updateNewProjectControls();
    }

    function renderNewProjectInspection(inspection) {
        const summary = element('new-project-source-summary');
        if (summary) summary.hidden = false;
        const title = text(inspection.title, 'Untitled source');
        const author = text(inspection.author, 'Author not found');
        const sourceTitle = element('new-project-source-title');
        const sourceAuthor = element('new-project-source-author');
        const filename = element('new-project-source-filename');
        const sourceLanguage = element('new-project-source-language-fact');
        const sourceFormat = element('new-project-source-format');
        const chapters = element('new-project-source-chapters');
        const fileName = element('new-project-file-name');
        const fileMeta = element('new-project-file-meta');
        const fileAction = element('new-project-file-action');
        if (sourceTitle) sourceTitle.textContent = title;
        if (sourceAuthor) sourceAuthor.textContent = author;
        if (filename) filename.textContent = text(inspection.filename);
        if (sourceLanguage) sourceLanguage.textContent = normalizedLanguage(inspection.language);
        if (sourceFormat) sourceFormat.textContent = String(inspection.source_type || 'source').toUpperCase();
        if (fileName) fileName.textContent = text(inspection.filename, 'Selected source');
        if (fileMeta) {
            const details = [
                String(inspection.source_type || 'source').toUpperCase(),
                formatBytes(inspection.size_bytes),
            ].filter(Boolean);
            fileMeta.textContent = details.join(' · ');
        }
        if (fileAction) fileAction.textContent = 'Change';
        if (chapters) {
            chapters.textContent = Number.isInteger(inspection.chapter_count)
                ? String(inspection.chapter_count)
                : Number.isInteger(inspection.entry_count)
                    ? `${inspection.entry_count} Script ${inspection.entry_count === 1 ? 'entry' : 'entries'}`
                    : 'Not identified';
        }
        const cover = element('new-project-cover');
        const coverImage = element('new-project-cover-image');
        if (cover) delete cover.dataset.hasCover;
        if (coverImage) {
            coverImage.hidden = true;
            coverImage.removeAttribute('src');
            coverImage.onload = null;
            coverImage.onerror = null;
            if (inspection.cover_data_url) {
                coverImage.onload = () => {
                    coverImage.hidden = false;
                    if (cover) cover.dataset.hasCover = 'true';
                };
                coverImage.onerror = () => {
                    coverImage.hidden = true;
                    coverImage.removeAttribute('src');
                    if (cover) delete cover.dataset.hasCover;
                };
                coverImage.src = inspection.cover_data_url;
            }
        }
        const projectName = element('new-project-name');
        const bookTitle = element('new-project-title');
        const authorInput = element('new-project-author');
        const sourceLanguageInput = element('new-project-source-language');
        const outputLanguageInput = element('new-project-output-language');
        if (projectName) projectName.value = text(inspection.suggested_project_name || inspection.title, 'Untitled project');
        if (bookTitle) bookTitle.value = title;
        if (authorInput) authorInput.value = inspection.author || '';
        const language = normalizedLanguage(inspection.language);
        if (!state.newProject.templateId) {
            if (sourceLanguageInput) sourceLanguageInput.value = language;
            if (outputLanguageInput) outputLanguageInput.value = language;
        }
    }

    async function inspectNewProjectFile(file) {
        if (!file) return;
        const request = ++state.newProject.inspectionRequest;
        const previousFile = state.newProject.sourceFile;
        const previousInspection = state.newProject.inspection;
        state.newProject.inspecting = true;
        state.newProject.completed = false;
        setNewProjectStatus('', 'info');
        updateNewProjectControls();
        const formData = new FormData();
        formData.append('generation_method', newProjectMethod());
        formData.append('source_file', file, file.name);
        try {
            const inspection = await fetchJson('/api/projects/inspect-source', {
                method: 'POST',
                body: formData,
            });
            if (request !== state.newProject.inspectionRequest) return;
            state.newProject.sourceFile = file;
            state.newProject.inspection = inspection;
            renderNewProjectInspection(inspection);
            setNewProjectStatus('Source validated. Confirm the extracted identity and project options.', 'success');
        } catch (error) {
            if (request !== state.newProject.inspectionRequest) return;
            state.newProject.sourceFile = previousFile;
            state.newProject.inspection = previousInspection;
            const sourceInput = element('new-project-source');
            if (sourceInput) sourceInput.value = '';
            const preserved = previousFile && previousInspection
                ? ' The previously validated source is still attached.'
                : '';
            setNewProjectStatus(`Source could not be used. ${error.message}.${preserved}`, 'error');
        } finally {
            if (request === state.newProject.inspectionRequest) {
                state.newProject.inspecting = false;
                updateNewProjectControls();
            }
        }
    }

    async function createNewProject() {
        const project = state.newProject;
        const modalElement = element('newProjectModal');
        if (project.completed) {
            if (modalElement) bootstrap.Modal.getOrCreateInstance(modalElement).hide();
            return;
        }
        const form = element('new-project-form');
        if (!form?.reportValidity()) return;
        if (!project.sourceFile || !project.inspection?.valid) {
            setNewProjectStatus('Choose and validate a source before creating the project.', 'error');
            updateNewProjectControls();
            return;
        }
        if (!state.catalog?.catalog_fingerprint) {
            await loadProjects({ silent: true });
        }
        if (!state.catalog?.catalog_fingerprint) {
            setNewProjectStatus('The project catalog is unavailable. Reload Projects and try again.', 'error');
            return;
        }

        project.creating = true;
        setNewProjectStatus('Creating the managed project. The source file is not modified.', 'info');
        updateNewProjectControls();
        const formData = new FormData();
        formData.append('project_name', element('new-project-name').value.trim());
        formData.append('book_title', element('new-project-title').value.trim());
        formData.append('author', element('new-project-author').value.trim());
        formData.append('source_language', element('new-project-source-language').value.trim());
        formData.append('output_language', element('new-project-output-language').value.trim());
        formData.append('generation_method', newProjectMethod());
        formData.append('preset', newProjectPreset());
        if (project.templateId) formData.append('template_id', project.templateId);
        formData.append('expected_catalog_fingerprint', state.catalog.catalog_fingerprint);
        formData.append('source_file', project.sourceFile, project.sourceFile.name);
        try {
            const result = await fetchJson('/api/projects', {
                method: 'POST',
                body: formData,
            });
            project.completed = true;
            project.createdProject = result.project || null;
            state.catalog.catalog_fingerprint = result.catalog_fingerprint || state.catalog.catalog_fingerprint;
            await loadProjects({ silent: true });
            const activation = result.activation || {};
            if (activation.state === 'current') {
                bootstrap.Modal.getOrCreateInstance(modalElement).hide();
                window.AlexandriaNavigation?.navigate(
                    activation.native_destination || 'script',
                    { project: result.project?.id }
                );
                return;
            }
            throw new Error(
                activation.message
                || 'The project was created but Alexandria did not activate it.'
            );
        } catch (error) {
            if (error.status === 409) {
                await loadProjects({ silent: true });
            }
            setNewProjectStatus(`Project could not be created. ${error.message}`, 'error');
        } finally {
            project.creating = false;
            updateNewProjectControls();
        }
    }

    function setupNewProject() {
        const modalElement = element('newProjectModal');
        const form = element('new-project-form');
        if (!modalElement || !form) return;
        window.addEventListener('alexandria:new-project-requested', event => {
            resetNewProjectForm();
            if (event.detail?.template) applyNewProjectTemplate(event.detail.template);
            bootstrap.Modal.getOrCreateInstance(modalElement).show();
        });
        modalElement.addEventListener('shown.bs.modal', () => {
            element('new-project-source')?.focus();
        });
        element('new-project-source')?.addEventListener('change', event => {
            inspectNewProjectFile(event.target.files?.[0] || null);
        });
        document.querySelectorAll('input[name="new-project-method"]').forEach(input => {
            input.addEventListener('change', () => {
                clearNewProjectTemplate();
                updateNewProjectAccept();
                if (state.newProject.sourceFile) {
                    inspectNewProjectFile(state.newProject.sourceFile);
                } else {
                    updateNewProjectControls();
                }
            });
        });
        document.querySelectorAll(
            '#new-project-name, #new-project-title, #new-project-author, #new-project-source-language, #new-project-output-language, input[name="new-project-preset"]'
        ).forEach(control => control.addEventListener('input', updateNewProjectControls));
        document.querySelectorAll(
            '#new-project-source-language, #new-project-output-language, input[name="new-project-preset"]'
        ).forEach(control => control.addEventListener('input', clearNewProjectTemplate));
        element('new-project-template-clear')?.addEventListener('click', clearNewProjectTemplate);
        form.addEventListener('submit', event => {
            event.preventDefault();
            createNewProject();
        });
    }

    function setupShellActions() {
        element('shell-primary-action')?.addEventListener('click', event => {
            const action = event.currentTarget.dataset.action;
            if (action === 'new-project') {
                window.dispatchEvent(new CustomEvent('alexandria:new-project-requested'));
            } else if (action === 'create-voice') {
                window.AlexandriaNavigation?.navigate('more', {
                    tool: 'voice-designer',
                    return: state.route.hash,
                });
            } else if (action === 'new-template') {
                openTemplateEditor();
            } else if (action === 'script-generate') {
                openLegacyScriptTool('generate');
            } else if (action === 'script-import-review') {
                openLegacyScriptTool('import');
            } else if (action === 'script-primary') {
                approveCurrentScript();
            } else if (action === 'continue-produce') {
                window.AlexandriaNavigation?.navigate('produce', state.route.context);
            } else if (action === 'continue-export') {
                window.AlexandriaNavigation?.navigate('export', state.route.context);
            } else if (action === 'produce-primary') {
                executeProduceMode('missing_stale');
            } else if (action === 'export-primary') {
                buildExport();
            }
        });
        element('rail-mobile-toggle')?.addEventListener('click', () => {
            const open = document.body.classList.toggle('rail-open');
            element('rail-mobile-toggle')?.setAttribute('aria-expanded', String(open));
        });
        document.addEventListener('click', event => {
            if (window.innerWidth > 760 || !document.body.classList.contains('rail-open')) return;
            if (event.target.closest('.alexandria-rail') || event.target.closest('#rail-mobile-toggle')) return;
            document.body.classList.remove('rail-open');
            element('rail-mobile-toggle')?.setAttribute('aria-expanded', 'false');
        });
    }

    async function loadRuntimeStatus() {
        const banner = element('runtime-restart-banner');
        const copy = element('runtime-restart-copy');
        if (!banner) return;
        try {
            const runtime = await fetchJson('/api/runtime_status');
            const changed = Array.isArray(runtime.changed_sources)
                ? runtime.changed_sources
                : [];
            banner.hidden = runtime.restart_required !== true;
            if (copy && runtime.restart_required === true) {
                const visible = changed.slice(0, 3).join(', ');
                const remainder = changed.length > 3
                    ? ` and ${changed.length - 3} more file${changed.length - 3 === 1 ? '' : 's'}`
                    : '';
                copy.textContent = visible
                    ? `The running process has not loaded changes to ${visible}${remainder}. Restart Alexandria from Pinokio before judging page behavior or performance.`
                    : 'The running process is older than the application files on disk. Restart Alexandria from Pinokio before judging page behavior or performance.';
            }
        } catch (error) {
            banner.hidden = false;
            if (copy) {
                copy.textContent = error?.status === 404
                    ? 'The running process does not expose the current runtime contract. Restart Alexandria from Pinokio before judging page behavior or performance.'
                    : `Runtime freshness could not be verified. Restart Alexandria from Pinokio if this page behaves differently from the current interface. ${error.message || ''}`.trim();
            }
        }
    }

    async function loadFlow(route) {
        if (!PROJECT_DESTINATIONS.has(route.destination)) {
            state.flow = null;
            setHeaderCopy(route);
            return;
        }
        try {
            state.flow = await fetchJson('/api/project_flow/status');
        } catch (error) {
            state.flow = null;
            showInlineStatus(`Project status could not be loaded. ${error.message}`, 'warning');
        }
        setHeaderCopy(route);
    }

    async function renderRoute(routeValue) {
        const route = routeApi.normalizeRoute(routeValue || routeApi.parseHash(window.location.hash));
        state.route = route;
        const liveRegion = element('canonical-shell-live');
        if (liveRegion) {
            liveRegion.textContent = '';
            delete liveRegion.dataset.state;
        }
        setDestinationVisibility(route);
        document.documentElement.classList.remove('alexandria-booting');
        setHeaderCopy(route);
        document.body.classList.remove('rail-open');
        if (route.destination !== 'script') document.body.classList.remove('script-legacy-mode');
        if (route.destination !== 'cast') {
            window.AlexandriaVoiceCardBridge?.releaseCast?.();
            state.cast.editing = false;
            state.cast.dirty = false;
            state.cast.saving = false;
            state.cast.editVoiceName = null;
            document.body.classList.remove('cast-voice-editing');
        }
        if (route.destination !== 'produce') {
            document.body.classList.remove('produce-legacy-mode');
            stopProducePolling();
        }
        if (route.destination !== 'export') {
            document.body.classList.remove('export-legacy-mode');
            stopExportPolling();
        }
        element('rail-mobile-toggle')?.setAttribute('aria-expanded', 'false');

        if (route.destination === 'projects') {
            await loadProjects({ silent: Boolean(state.catalog) });
        }
        await loadFlow(route);
        if (route.destination === 'script') {
            await loadScriptReview();
        } else if (route.destination === 'cast') {
            applyCastRouteContext(route);
            await loadCast();
        } else if (route.destination === 'produce') {
            await loadProduce();
        } else if (route.destination === 'export') {
            await loadExport();
        } else if (route.destination === 'library') {
            applyLibraryRouteContext(route);
            await loadLibrary();
        } else if (route.destination === 'voices') {
            applyLibraryRouteContext(route);
            await loadVoices();
        } else if (route.destination === 'templates') {
            applyTemplateRouteContext(route);
            await loadTemplates();
        } else if (route.destination === 'settings') {
            await loadSettings();
        } else if (route.destination === 'more' && !route.context.tool) {
            applyMoreRouteContext(route);
            await loadMoreTools();
        } else if (route.destination === 'more' && route.context.tool === 'help-center') {
            await loadHelpCenter();
        } else if (route.destination === 'more' && ['maintenance', 'model-cache'].includes(route.context.tool)) {
            await openMaintenanceMode(route);
        }
    }

    function initialize() {
        mountCanonicalWorkspaces();
        setupProjectHome();
        setupNewProject();
        setupScriptReview();
        setupCast();
        setupProduce();
        setupExport();
        setupSupportingDestinations();
        setupTemplates();
        setupSettings();
        setupMaintenance();
        setupMore();
        setupPersistentPlayer();
        setupShellActions();
        loadRuntimeStatus();
        window.setInterval(loadRuntimeStatus, 30000);
        renderRoute(state.route);
    }

    window.addEventListener('alexandria:routechange', event => {
        renderRoute(event.detail?.route || routeApi.parseHash(window.location.hash));
    });

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initialize, { once: true });
    } else {
        initialize();
    }

    window.AlexandriaCanonicalInterface = Object.freeze({
        renderRoute,
        loadProjects,
        state: () => ({ ...state }),
    });
})();
