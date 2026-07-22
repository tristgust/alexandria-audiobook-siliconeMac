(() => {
    'use strict';

    const REVIEW_FIELDS = [
        ['source_identity_retention_passed', 'Same speaker identity'],
        ['identity_drift_passed', 'No identity drift'],
        ['emotion_match_passed', 'Emotion matches style'],
        ['pronunciation_passed', 'Pronunciation is correct'],
        ['pace_passed', 'Pace is usable'],
    ];
    const COMPARISON_REVIEW_FIELDS = [
        ['source_identity_retention_passed', 'Owned identity retained'],
        ['identity_consistency_passed', 'Identity consistent across lines'],
        ['emotion_match_passed', 'Emotion routing is accurate'],
        ['pronunciation_passed', 'Pronunciation is correct'],
        ['pace_passed', 'Pace is usable'],
        ['long_form_drift_passed', 'No long-form drift concern'],
    ];

    function statusEntry(characterId) {
        return (window.expressiveReferenceBankStatus?.entries || []).find(
            entry => entry.character_id === characterId
        ) || null;
    }

    function referenceAudioUrl(characterId, referenceId) {
        return `/api/expressive_reference_banks/${encodeURIComponent(characterId)}/audio/reference/${encodeURIComponent(referenceId)}`;
    }

    function comparisonAudioUrl(characterId, lineIndex, mode) {
        return `/api/expressive_reference_banks/${encodeURIComponent(characterId)}/audio/comparison/${Number(lineIndex)}/${encodeURIComponent(mode)}`;
    }

    function ownedClipOptions(project) {
        return (project.existing_recordings?.clips || [])
            .filter(clip => clip.inclusion_decision === 'included')
            .map(clip => {
                const label = [
                    clip.style_label || 'Owned clip',
                    clip.transcript || clip.clip_id,
                ].filter(Boolean).join(' — ');
                return `<option value="${escapeHtml(clip.clip_id)}">${escapeHtml(label)}</option>`;
            }).join('');
    }

    function reviewMarkup(reference, locked) {
        const review = reference.review || {};
        return `
            <div class="reference-bank-review-grid">
                ${REVIEW_FIELDS.map(([field, label]) => `
                    <div class="form-check">
                        <input class="form-check-input" type="checkbox"
                            id="reference-review-${escapeHtml(reference.reference_id)}-${field}"
                            data-reference-review-field="${field}"
                            ${review[field] ? 'checked' : ''}
                            ${locked ? 'disabled' : ''}>
                        <label class="form-check-label" for="reference-review-${escapeHtml(reference.reference_id)}-${field}">${escapeHtml(label)}</label>
                    </div>
                `).join('')}
            </div>
            <label class="form-label" for="reference-review-notes-${escapeHtml(reference.reference_id)}">Listening notes</label>
            <textarea class="form-control form-control-sm" rows="2"
                id="reference-review-notes-${escapeHtml(reference.reference_id)}"
                data-reference-review-notes
                ${locked ? 'readonly' : ''}>${escapeHtml(review.notes || '')}</textarea>
            ${locked ? '' : `
                <button type="button" class="btn btn-sm btn-outline-primary mt-2"
                    data-reference-bank-action="review-reference"
                    data-reference-id="${escapeHtml(reference.reference_id)}">Save listening review</button>
            `}
        `;
    }

    function comparisonReviewMarkup(comparison, locked) {
        return `
            <div class="reference-bank-review-grid">
                ${COMPARISON_REVIEW_FIELDS.map(([field, label]) => `
                    <div class="form-check">
                        <input class="form-check-input" type="checkbox"
                            id="reference-comparison-review-${field}"
                            data-reference-comparison-review-field="${field}"
                            ${comparison[field] ? 'checked' : ''}
                            ${locked ? 'disabled' : ''}>
                        <label class="form-check-label" for="reference-comparison-review-${field}">${escapeHtml(label)}</label>
                    </div>
                `).join('')}
            </div>
            <label class="form-label" for="reference-comparison-notes">Comparison notes</label>
            <textarea class="form-control form-control-sm" rows="2" id="reference-comparison-notes" ${locked ? 'readonly' : ''}>${escapeHtml(comparison.notes || '')}</textarea>
            ${locked ? '' : `
                <button type="button" class="btn btn-sm btn-outline-primary mt-2" data-reference-bank-action="review-comparison">Save comparison review</button>
            `}
        `;
    }

    function comparisonLineMarkup(text = '', instruct = '', removable = true) {
        return `
            <div class="reference-comparison-line" data-reference-comparison-line>
                <div>
                    <label class="form-label">Fixed comparison text</label>
                    <textarea class="form-control form-control-sm" rows="2" data-reference-comparison-text>${escapeHtml(text)}</textarea>
                </div>
                <div>
                    <label class="form-label">Delivery instruction</label>
                    <textarea class="form-control form-control-sm" rows="2" data-reference-comparison-instruct>${escapeHtml(instruct)}</textarea>
                </div>
                <button type="button" class="btn btn-sm btn-link text-secondary" data-reference-bank-action="remove-comparison-line" ${removable ? '' : 'disabled'}>Remove</button>
            </div>
        `;
    }

    function unavailableMarkup(project) {
        const selected = project.selected_reference_sample;
        const ready = Boolean(
            project.desired_base_persona?.approval_status === 'approved'
            && project.existing_recordings
            && selected?.source_kind === 'existing_recordings'
        );
        return `
            <div class="voice-project-section-header">
                <div>
                    <h4>Expressive reference bank</h4>
                    <p class="voice-project-section-copy">Build a reviewed set of delivery references from one approved owned-recording identity. Generated variants remain experimental until every reference and the fixed comparison set pass listening review.</p>
                </div>
                <span class="stage-page-state" data-state="${ready ? 'warning' : 'idle'}">${ready ? 'Ready to create' : 'Not available yet'}</span>
            </div>
            ${ready ? `
                <dl class="voice-project-facts">
                    <div><dt>Identity source</dt><dd>Approved owned recording</dd></div>
                    <div><dt>Source clip</dt><dd>${escapeHtml(selected.clip_id)}</dd></div>
                    <div><dt>Approval model</dt><dd>Manual listening; no automatic production assignment</dd></div>
                </dl>
                <div class="tool-command-bar mt-3">
                    <span class="workflow-help">Creation copies no new identity. The selected owned clip remains authoritative.</span>
                    <button type="button" class="btn btn-primary" data-reference-bank-action="create">Create reference bank</button>
                </div>
            ` : `
                <ul class="voice-blocker-list">
                    ${project.desired_base_persona?.approval_status === 'approved' ? '' : '<li>Approve the voice persona.</li>'}
                    ${project.existing_recordings ? '' : '<li>Create an owned-recording preparation project.</li>'}
                    ${selected?.source_kind === 'existing_recordings' ? '' : '<li>Approve the recording dataset and select its identity reference.</li>'}
                </ul>
            `}
        `;
    }

    function styleCardMarkup({project, bank, styleKey, definition, reference, locked, options}) {
        const state = reference?.review?.approved
            ? 'saved'
            : reference
                ? 'warning'
                : 'idle';
        const label = reference?.review?.approved
            ? 'Approved'
            : reference
                ? 'Review required'
                : 'Missing';
        const source = reference
            ? humanizeVoiceTrainingValue(reference.source_kind)
            : 'No reference';
        const canReplace = !locked && styleKey !== bank.neutral_style_key;
        return `
            <article class="reference-bank-style" data-reference-style-card="${escapeHtml(styleKey)}">
                <div class="reference-bank-style-header">
                    <div>
                        <h5>${escapeHtml(definition.label || humanizeVoiceTrainingValue(styleKey))}</h5>
                        <div class="reference-bank-style-meta">${escapeHtml(source)}${reference?.model && reference.model !== 'none' ? ` · ${escapeHtml(reference.model)}` : ''}</div>
                    </div>
                    <span class="stage-page-state" data-state="${state}">${label}</span>
                </div>
                ${reference ? `
                    <audio controls preload="none" src="${referenceAudioUrl(project.character.id, reference.reference_id)}"></audio>
                    <p class="workflow-help mb-1"><strong>Direction:</strong> ${escapeHtml(reference.instruction)}</p>
                    <p class="workflow-help mb-0"><strong>Text:</strong> ${escapeHtml(reference.reference_text)}</p>
                    ${reviewMarkup(reference, locked)}
                ` : `
                    <p class="workflow-help">No reviewed ${escapeHtml(definition.label || styleKey)} reference exists yet.</p>
                `}
                ${canReplace ? `
                    <details class="utility-disclosure mt-2">
                        <summary>${reference ? 'Replace reference' : 'Add reference'}</summary>
                        <div class="utility-disclosure-body reference-bank-style-controls">
                            <div>
                                <label class="form-label" for="reference-text-${escapeHtml(styleKey)}">Spoken reference text</label>
                                <textarea class="form-control form-control-sm" rows="2" id="reference-text-${escapeHtml(styleKey)}" data-reference-style-text="${escapeHtml(styleKey)}">${escapeHtml(reference?.reference_text || project.desired_base_persona.ref_text || '')}</textarea>
                            </div>
                            <div>
                                <label class="form-label" for="reference-instruction-${escapeHtml(styleKey)}">Delivery instruction</label>
                                <textarea class="form-control form-control-sm" rows="2" id="reference-instruction-${escapeHtml(styleKey)}" data-reference-style-instruction="${escapeHtml(styleKey)}">${escapeHtml(reference?.instruction || definition.instruction || '')}</textarea>
                            </div>
                            <div class="reference-bank-style-actions">
                                <button type="button" class="btn btn-sm btn-outline-primary" data-reference-bank-action="generate-reference" data-style-key="${escapeHtml(styleKey)}">Generate controlled variant</button>
                                ${options ? `
                                    <select class="form-select form-select-sm" aria-label="Owned clip for ${escapeHtml(definition.label || styleKey)}" data-reference-owned-clip="${escapeHtml(styleKey)}">
                                        <option value="">Choose owned clip…</option>
                                        ${options}
                                    </select>
                                    <button type="button" class="btn btn-sm btn-outline-secondary" data-reference-bank-action="use-owned-reference" data-style-key="${escapeHtml(styleKey)}">Use owned clip</button>
                                ` : ''}
                            </div>
                            <p class="workflow-help mb-0">Generated variants use the approved owned clip only as identity input. They still require full listening review.</p>
                        </div>
                    </details>
                ` : ''}
            </article>
        `;
    }

    function render(project) {
        const host = document.getElementById('voice-reference-bank-section');
        if (!host || project.character.id !== window.voiceTrainingSelectedId) return;
        const entry = statusEntry(project.character.id);
        const bank = window.expressiveReferenceBank;
        if (entry?.status === 'invalid') {
            host.innerHTML = `
                <div class="voice-project-section-header">
                    <div>
                        <h4>Expressive reference bank</h4>
                        <p class="voice-project-section-copy">${escapeHtml(entry.error || 'The saved reference bank is invalid.')}</p>
                    </div>
                    <span class="stage-page-state" data-state="error">Needs attention</span>
                </div>
                <button type="button" class="btn btn-sm btn-outline-secondary" data-reference-bank-action="refresh">Refresh</button>
            `;
            return;
        }
        if (!entry || entry.status === 'absent' || !bank) {
            host.innerHTML = unavailableMarkup(project);
            return;
        }

        const locked = bank.status === 'approved';
        const definitions = window.expressiveReferenceBankStatus?.style_definitions || {};
        const references = new Map(
            (bank.references || []).map(reference => [reference.style_key, reference])
        );
        const requiredStyles = bank.required_style_keys || Object.keys(definitions);
        const approvedCount = requiredStyles.filter(
            styleKey => references.get(styleKey)?.review?.approved === true
        ).length;
        const allStylesApproved = approvedCount === requiredStyles.length;
        const comparison = bank.comparison || {
            status: 'not_started',
            outputs: [],
            test_lines: [],
        };
        const comparisonApproved = comparison.status === 'approved';
        const assignment = bank.production_assignment || {status: 'unassigned'};
        const options = ownedClipOptions(project);
        const styleCards = requiredStyles.map(styleKey => styleCardMarkup({
            project,
            bank,
            styleKey,
            definition: definitions[styleKey] || {
                label: humanizeVoiceTrainingValue(styleKey),
                instruction: '',
            },
            reference: references.get(styleKey),
            locked,
            options,
        })).join('');

        const lines = comparison.test_lines?.length
            ? comparison.test_lines.map((text, index) => comparisonLineMarkup(
                text,
                '',
                comparison.test_lines.length > 1
            )).join('')
            : [
                comparisonLineMarkup(
                    project.desired_base_persona.ref_text || '',
                    'Natural neutral delivery.',
                    false
                ),
                comparisonLineMarkup(
                    '',
                    'Urgent delivery with clear words and stable identity.',
                    true
                ),
            ].join('');
        const outputLabels = {
            reference_bank_clone: 'Reference bank',
            single_reference_clone: 'Single reference',
            direct_voice_design: 'Direct design comparator',
        };
        const outputs = (comparison.outputs || []).map(output => `
            <div class="reference-comparison-output">
                <strong>${escapeHtml(outputLabels[output.mode] || humanizeVoiceTrainingValue(output.mode))}</strong>
                <span>Line ${Number(output.line_index) + 1}${output.style_key ? ` · ${escapeHtml(humanizeVoiceTrainingValue(output.style_key))}` : ''}</span>
                <audio controls preload="none" src="${comparisonAudioUrl(project.character.id, output.line_index, output.mode)}"></audio>
            </div>
        `).join('');

        host.innerHTML = `
            <div class="voice-project-section-header">
                <div>
                    <h4>Expressive reference bank</h4>
                    <p class="voice-project-section-copy">Review each identity-preserving delivery reference, then compare the bank against a single-reference clone and a direct-design comparator before returning to Cast for production assignment.</p>
                </div>
                <span class="stage-page-state" data-state="${bank.status === 'approved' ? 'saved' : 'warning'}">${escapeHtml(humanizeVoiceTrainingValue(bank.status))}</span>
            </div>
            <dl class="reference-bank-summary">
                <div><dt>Identity authority</dt><dd>${escapeHtml(humanizeVoiceTrainingValue(bank.identity_source.kind))}</dd></div>
                <div><dt>Approved styles</dt><dd>${approvedCount} / ${requiredStyles.length}</dd></div>
                <div><dt>Comparison</dt><dd>${escapeHtml(humanizeVoiceTrainingValue(comparison.status))}</dd></div>
                <div><dt>Production</dt><dd>${escapeHtml(humanizeVoiceTrainingValue(assignment.status))}</dd></div>
            </dl>
            <div class="reference-bank-style-grid">${styleCards}</div>

            <details class="utility-disclosure mt-3" ${comparison.status !== 'not_started' ? 'open' : ''}>
                <summary>Fixed listening comparison</summary>
                <div class="utility-disclosure-body">
                    <p class="workflow-help">Use identical text across the reference bank, single neutral reference, and external direct-design comparator. The comparator is not an identity candidate.</p>
                    <div class="reference-bank-comparison-lines" id="reference-bank-comparison-lines">${lines}</div>
                    ${locked ? '' : `
                        <div class="reference-bank-global-actions">
                            <button type="button" class="btn btn-sm btn-outline-secondary" data-reference-bank-action="add-comparison-line">Add line</button>
                            <button type="button" class="btn btn-sm btn-outline-primary" data-reference-bank-action="generate-comparison" ${references.get(bank.neutral_style_key)?.review?.approved ? '' : 'disabled'}>Generate comparison audio</button>
                        </div>
                    `}
                    ${outputs ? `<div class="reference-comparison-output-grid">${outputs}</div>` : ''}
                    ${['generated', 'approved', 'rejected'].includes(comparison.status)
                        ? comparisonReviewMarkup(comparison, locked)
                        : ''}
                </div>
            </details>

            <div class="tool-command-bar mt-3">
                <span class="workflow-help">
                    ${bank.status === 'approved'
                        ? assignment.status === 'assigned'
                            ? `Currently assigned to ${escapeHtml(assignment.voice_name || project.character.canonical_name)}. Change or remove that assignment in Cast.`
                            : 'Approved. Return to Cast to assign it as the production Voice.'
                        : allStylesApproved && comparisonApproved
                            ? 'All listening gates passed. Approval still requires an explicit action.'
                            : 'Approve every required style and the fixed comparison before approving the bank.'}
                </span>
                <div class="reference-bank-global-actions">
                    <button type="button" class="btn btn-sm btn-outline-secondary" data-reference-bank-action="refresh">Refresh</button>
                    ${bank.status === 'approved' ? `
                        <button type="button" class="btn btn-sm btn-primary" data-reference-bank-action="open-cast">Open Cast assignment</button>
                        ${assignment.status === 'assigned'
                            ? ''
                            : '<button type="button" class="btn btn-sm btn-link text-secondary" data-reference-bank-action="return-to-draft">Return to draft</button>'}
                    ` : `
                        <button type="button" class="btn btn-sm btn-primary" data-reference-bank-action="approve-bank" ${allStylesApproved && comparisonApproved ? '' : 'disabled'}>Approve reference bank</button>
                    `}
                </div>
            </div>
        `;
    }

    async function updateStatus() {
        window.expressiveReferenceBankStatus = await API.get(
            '/api/expressive_reference_banks/status'
        );
    }

    async function refresh(characterId = window.voiceTrainingSelectedId) {
        if (!characterId || !window.voiceTrainingProject) return;
        const host = document.getElementById('voice-reference-bank-section');
        if (host) {
            host.innerHTML = '<div class="reference-bank-processing"><span class="spinner-border spinner-border-sm" aria-hidden="true"></span><span>Checking expressive references…</span></div>';
        }
        try {
            await updateStatus();
            if (window.voiceTrainingSelectedId !== characterId) return;
            const entry = statusEntry(characterId);
            window.expressiveReferenceBank = null;
            if (entry && !['absent', 'invalid'].includes(entry.status)) {
                window.expressiveReferenceBank = await API.get(
                    `/api/expressive_reference_banks/${encodeURIComponent(characterId)}`
                );
            }
            if (window.voiceTrainingSelectedId !== characterId) return;
            render(window.voiceTrainingProject);
        } catch (error) {
            if (window.voiceTrainingSelectedId !== characterId || !host) return;
            host.innerHTML = `
                <div class="voice-project-section-header">
                    <div>
                        <h4>Expressive reference bank</h4>
                        <p class="voice-project-section-copy">${escapeHtml(error.message)}</p>
                    </div>
                    <span class="stage-page-state" data-state="error">Unavailable</span>
                </div>
                <button type="button" class="btn btn-sm btn-outline-secondary" data-reference-bank-action="refresh">Try again</button>
            `;
        }
    }

    async function mutate(action, payload = {}) {
        if (!window.voiceTrainingSelectedId || !window.expressiveReferenceBank) return null;
        try {
            const bank = await API.post(
                `/api/expressive_reference_banks/${encodeURIComponent(window.voiceTrainingSelectedId)}/action`,
                {
                    bank_fingerprint: window.expressiveReferenceBank.bank_fingerprint,
                    action,
                    payload,
                }
            );
            window.expressiveReferenceBank = bank;
            await updateStatus();
            render(window.voiceTrainingProject);
            return bank;
        } catch (error) {
            showToast(error.message, 'error');
            if (error.status === 409 || error.code === 'stale_expressive_reference_bank') {
                await refresh();
            }
            return null;
        }
    }

    async function create(button) {
        const project = window.voiceTrainingProject;
        const sourceClipId = project?.selected_reference_sample?.clip_id;
        if (!sourceClipId) {
            showToast('Select an approved owned-recording reference first.', 'warning');
            return;
        }
        button.disabled = true;
        try {
            window.expressiveReferenceBank = await API.post(
                `/api/expressive_reference_banks/${encodeURIComponent(window.voiceTrainingSelectedId)}/create`,
                {source_clip_id: sourceClipId}
            );
            await updateStatus();
            render(project);
            showToast('Expressive reference bank created for listening review.', 'success');
        } catch (error) {
            button.disabled = false;
            showToast(error.message, 'error');
        }
    }

    async function generateReference(styleKey, button) {
        const text = document.querySelector(
            `[data-reference-style-text="${CSS.escape(styleKey)}"]`
        )?.value.trim() || '';
        const instruction = document.querySelector(
            `[data-reference-style-instruction="${CSS.escape(styleKey)}"]`
        )?.value.trim() || '';
        if (!text || !instruction) {
            showToast('Reference text and delivery instruction are required.', 'warning');
            return;
        }
        button.disabled = true;
        try {
            const result = await API.post(
                `/api/expressive_reference_banks/${encodeURIComponent(window.voiceTrainingSelectedId)}/generate`,
                {
                    bank_fingerprint: window.expressiveReferenceBank.bank_fingerprint,
                    style_key: styleKey,
                    reference_text: text,
                    instruction,
                }
            );
            window.expressiveReferenceBank = result.bank;
            await updateStatus();
            render(window.voiceTrainingProject);
            showToast(`${humanizeVoiceTrainingValue(styleKey)} reference generated. Listen and review it before approval.`, 'success');
        } catch (error) {
            button.disabled = false;
            showToast(error.message, 'error');
            if (error.status === 409) await refresh();
        }
    }

    async function useOwnedReference(styleKey, button) {
        const clipId = document.querySelector(
            `[data-reference-owned-clip="${CSS.escape(styleKey)}"]`
        )?.value || '';
        const instruction = document.querySelector(
            `[data-reference-style-instruction="${CSS.escape(styleKey)}"]`
        )?.value.trim() || '';
        if (!clipId) {
            showToast('Choose an included owned recording clip.', 'warning');
            return;
        }
        button.disabled = true;
        const result = await mutate('add_owned_recording_reference', {
            style_key: styleKey,
            source_clip_id: clipId,
            instruction: instruction || undefined,
        });
        if (result) {
            showToast('Owned recording added as the style reference. Listen and review it before approval.', 'success');
        } else {
            button.disabled = false;
        }
    }

    async function reviewReference(referenceId) {
        const button = document.querySelector(
            `[data-reference-id="${CSS.escape(referenceId)}"]`
        );
        const card = button?.closest('[data-reference-style-card]');
        if (!card) return;
        const payload = {reference_id: referenceId};
        card.querySelectorAll('[data-reference-review-field]').forEach(input => {
            payload[input.dataset.referenceReviewField] = input.checked;
        });
        payload.notes = card.querySelector('[data-reference-review-notes]')?.value.trim() || '';
        const bank = await mutate('review_reference', payload);
        if (!bank) return;
        const approved = bank.references.find(
            reference => reference.reference_id === referenceId
        )?.review?.approved;
        showToast(
            approved
                ? 'Reference approved.'
                : 'Reference review saved; failed gates remain visible.',
            approved ? 'success' : 'warning'
        );
    }

    function addComparisonLine() {
        document.getElementById('reference-bank-comparison-lines')?.insertAdjacentHTML(
            'beforeend',
            comparisonLineMarkup('', '', true)
        );
    }

    function comparisonPayload() {
        return [...document.querySelectorAll('[data-reference-comparison-line]')]
            .map(row => ({
                text: row.querySelector('[data-reference-comparison-text]')?.value.trim() || '',
                instruct: row.querySelector('[data-reference-comparison-instruct]')?.value.trim() || '',
            }))
            .filter(line => line.text);
    }

    async function generateComparison(button) {
        const lines = comparisonPayload();
        if (!lines.length) {
            showToast('Provide at least one fixed comparison line.', 'warning');
            return;
        }
        button.disabled = true;
        try {
            const result = await API.post(
                `/api/expressive_reference_banks/${encodeURIComponent(window.voiceTrainingSelectedId)}/compare`,
                {
                    bank_fingerprint: window.expressiveReferenceBank.bank_fingerprint,
                    lines,
                }
            );
            window.expressiveReferenceBank = result.bank;
            await updateStatus();
            render(window.voiceTrainingProject);
            showToast('Comparison audio generated. Listen to every mode before recording the result.', 'success');
        } catch (error) {
            button.disabled = false;
            showToast(error.message, 'error');
            if (error.status === 409) await refresh();
        }
    }

    async function reviewComparison() {
        const payload = {};
        document.querySelectorAll('[data-reference-comparison-review-field]').forEach(input => {
            payload[input.dataset.referenceComparisonReviewField] = input.checked;
        });
        payload.notes = document.getElementById('reference-comparison-notes')?.value.trim() || '';
        const bank = await mutate('review_comparison', payload);
        if (!bank) return;
        showToast(
            bank.comparison.status === 'approved'
                ? 'Comparison approved.'
                : 'Comparison review saved; failed gates block approval.',
            bank.comparison.status === 'approved' ? 'success' : 'warning'
        );
    }

    async function handleAction(button) {
        const action = button.dataset.referenceBankAction;
        if (action === 'refresh') {
            await refresh();
        } else if (action === 'create') {
            await create(button);
        } else if (action === 'generate-reference') {
            await generateReference(button.dataset.styleKey, button);
        } else if (action === 'use-owned-reference') {
            await useOwnedReference(button.dataset.styleKey, button);
        } else if (action === 'review-reference') {
            await reviewReference(button.dataset.referenceId);
        } else if (action === 'add-comparison-line') {
            addComparisonLine();
        } else if (action === 'remove-comparison-line') {
            button.closest('[data-reference-comparison-line]')?.remove();
        } else if (action === 'generate-comparison') {
            await generateComparison(button);
        } else if (action === 'review-comparison') {
            await reviewComparison();
        } else if (action === 'approve-bank') {
            const confirmed = await showConfirm(
                'Approve this expressive reference bank? Every required reference and the fixed comparison must already have passed listening review.'
            );
            if (confirmed) {
                const bank = await mutate('approve_bank', {});
                if (bank) showToast('Reference bank approved. Production assignment is still separate.', 'success');
            }
        } else if (action === 'return-to-draft') {
            if (window.expressiveReferenceBank?.production_assignment?.status === 'assigned') {
                showToast(
                    'Change or remove the production assignment in Cast before returning this bank to draft.',
                    'warning'
                );
                return;
            }
            const confirmed = await showConfirm(
                'Return this approved reference bank to draft for further review?'
            );
            if (confirmed) await mutate('return_to_draft', {});
        } else if (action === 'open-cast') {
            window.openCastAssignmentForCharacter?.();
        }
    }

    document.getElementById('voice-projects-detail')?.addEventListener(
        'click',
        async event => {
            const button = event.target.closest('[data-reference-bank-action]');
            if (!button || button.disabled) return;
            event.preventDefault();
            await handleAction(button);
        }
    );

    window.refreshExpressiveReferenceBank = refresh;
    window.renderExpressiveReferenceBank = render;
})();
