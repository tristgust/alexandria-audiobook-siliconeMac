'use strict';

const UI = globalThis.AlexandriaUI;

function text(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value == null ? '' : String(value);
  return node;
}

export function createScriptWorkflowState({
  creationStep,
  approvalStep,
  deliveryPlan,
  pronunciationGuidance,
  completedDeliveryTaskSection,
  directImportDisclosure,
}) {
  const root = document.createElement('div');
  root.className = 'script-workflow-sequence';
  const currentAction = document.createElement('div');
  currentAction.className = 'script-workflow-current';
  currentAction.dataset.scriptWorkflowCurrent = '';
  const currentActionBody = document.createElement('div');
  currentActionBody.className = 'script-workflow-current__body';
  currentAction.append(
    text('p', 'script-workflow-current__label', 'Current action'),
    currentActionBody,
  );

  const previousWorkContent = document.createElement('div');
  previousWorkContent.className = 'script-workflow-history__content';
  const previousWork = UI.disclosure({
    label: 'Create or replace the Script',
    description: 'Generation, task import, and direct replacement options.',
    content: previousWorkContent,
  });
  previousWork.classList.add('script-workflow-history');
  root.append(currentAction, previousWork);

  const previousWorkTrigger = previousWork.querySelector('.disclosure__trigger');
  const previousWorkPanel = previousWork.querySelector('.disclosure__panel');
  const setPreviousWorkCopy = (label, description) => {
    const labelNode = previousWorkTrigger.querySelector('strong');
    const descriptionNode = previousWorkTrigger.querySelector('small');
    if (labelNode) labelNode.textContent = label;
    if (descriptionNode) descriptionNode.textContent = description;
  };
  const collapsePreviousWork = () => {
    previousWorkPanel.hidden = true;
    previousWorkTrigger.setAttribute('aria-expanded', 'false');
  };

  let renderedStage = '';
  const render = (approval) => {
    const stage = approval.accepted
      ? 'delivery'
      : approval.hasScript ? (approval.pending ? 'approving' : 'approval') : 'creation';
    currentAction.dataset.stage = stage;
    if (stage === renderedStage) return stage;
    renderedStage = stage;
    if (stage === 'delivery') {
      currentActionBody.replaceChildren(
        deliveryPlan.root,
        completedDeliveryTaskSection,
        pronunciationGuidance.root,
      );
      previousWorkContent.replaceChildren(
        creationStep,
        directImportDisclosure,
      );
      previousWork.hidden = false;
      setPreviousWorkCopy(
        'Change or replace the Script',
        'Generate, import, or replace the approved Script. Any change returns it to review.',
      );
      collapsePreviousWork();
      return stage;
    }
    if (stage === 'creation') {
      currentActionBody.replaceChildren(creationStep, directImportDisclosure);
      previousWorkContent.replaceChildren(approvalStep);
      previousWork.hidden = true;
      return stage;
    }
    currentActionBody.replaceChildren(approvalStep);
    previousWorkContent.replaceChildren(creationStep, directImportDisclosure);
    previousWork.hidden = false;
    setPreviousWorkCopy(
      'Create or replace the Script',
      'Generation, task import, and direct replacement options.',
    );
    collapsePreviousWork();
    return stage;
  };

  return Object.freeze({ root, render });
}
