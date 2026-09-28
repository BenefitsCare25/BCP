'use strict';
const screens = {
  claim: {title:'Explain the claim decision',context:'Assessor · M1: claim safeguards',summary:'Keep the evidence, payment amount and member explanation together in the existing claims workspace.',role:'Claims assessor',image:'02-claim-decision.png',alt:'Proposed claims workspace: evidence on the left, an approval reduced from S$150 to S$120 on the right, a required reason and member explanation, a separate internal note, and a preview of the member outcome.',notes:[['Evidence beside the decision','Check source documents and policy references without losing the draft decision.'],['Explain a reduction or rejection','A member-facing reason is required for an adverse outcome. An AI flag alone is not an explanation.'],['Keep the audiences separate','Internal assessment notes remain broker-only. The member preview shows the public explanation and amount.']]},
  member: {title:'Make AI assistance understandable',context:'Member · M1: disclosure / M3: reconsideration',summary:'Explain AI before extraction, preserve manual entry, and make the reason for a decision easy to find.',role:'Member',image:'04-member-claim-journey.png',alt:'Two proposed mobile claim screens. Intake explains AI autofill before upload and offers manual entry. The claim outcome shows S$120 approved from S$150 claimed, an explanation, and Request another review.',notes:[['Disclose before extraction','Explain AI suggestions before the first extraction request. Manual entry skips autofill; submitted documents may still be checked with AI.'],['Show the reason next to the amount','A person makes the claim decision. The outcome presents the assessor’s explanation in plain language.'],['Preserve the original decision','A reconsideration request creates a separate review record. It does not reverse the decision or cancel a payment.']]},
  firm: {title:'Know who owns each AI use',context:'Firm administrator · M2: oversight workspace',summary:'A firm-wide register connects each use to its owner, evidence and next action.',role:'Broker firm administrator',image:'01-firm-ai-oversight.png',alt:'Proposed firm AI oversight register with claim review, claim autofill and slip extraction. Columns distinguish owner, deployment, evidence and next action. A selected claim review opens a purpose and next-action panel.',notes:[['Keep the firm scope visible','Enter from the existing firm tools in the top bar. Firm assurance records belong to the broker firm.'],['Separate operation from assurance','An active AI use may still need evidence or an independent evaluation. Active is not a compliance claim.'],['Put the next action in context','Review purpose, risks, evidence and history beside the selected use, with an explicit owner for follow-up.']]},
  release: {title:'Validate before approving a release',context:'Platform administrator · M2: validation and releases',summary:'A successful connection is one check. Independent evaluation and a specific approved configuration govern activation.',role:'Platform release approver',image:'03-release-validation.png',alt:'Proposed platform release review. The candidate is blocked because independent validation is missing. Connection passed, held-out evaluation is not measured, reviewer sign-off is missing, and approval is disabled. Existing deployment remains active.',notes:[['Distinguish connection from validation','A provider connection confirms connectivity. Evaluation needs case counts, expected outcomes and reviewed acceptance criteria.'],['Tie approval to exact evidence','The approved manifest includes model, prompt, configuration and threshold versions. Missing required evidence blocks approval.'],['Keep activation deliberate','Approval and activation are separate. A blocked candidate leaves the current release unchanged; manual handling remains available.']]},
  evidence: {title:'Keep policy and evidence attributable',context:'Firm administrator · M2: companion wireframe',summary:'Use a versioned register with an owner, reviewer and review date for each governance record.',role:'Firm administrator',image:'qa-pdf-page-6.png',wireframe:true,alt:'Evidence and policy wireframe showing AI policy Draft 1 needing action, assessor training not recorded, data processing review due, and a selected policy with no assigned reviewer.',notes:[['An approval belongs to a version','Capture who approved the document and when. Editing an approved record creates a new draft.'],['Make the missing step explicit','An unassigned reviewer stays visible with an action to assign one. Do not mark the policy approved by default.'],['Respect restricted evidence','Individual staff records and sensitive attachments require narrower access. Reviewed evidence is not a certification badge.']]},
  suppliers: {title:'Verify the data and supplier commitments',context:'Platform operator · M2: companion wireframe',summary:'Connect the configured service to verified agreements, data categories and retention decisions.',role:'Platform operator / privacy owner',image:'qa-pdf-page-7.png',wireframe:true,alt:'Data and supplier wireframe for Google Vertex AI. Configured Singapore endpoint is shown separately from processing agreement not linked, retention and training-use terms needing verification, retention schedule awaiting approval, and deletion procedure not linked.',notes:[['Configuration is one piece of evidence','A Singapore endpoint setting does not establish every supplier processing commitment. Link verified terms.'],['Keep unknowns visible','Unverified retention and training-use terms remain unresolved, with a named owner and action.'],['Separate credentials and evidence','Credentials stay in the authorized AI Provider settings. General exports omit raw medical documents by default.']]}
};
const image = document.querySelector('#screen-image');
const viewport = document.querySelector('#image-viewport');
const zoom = document.querySelector('#zoom');
const error = document.querySelector('#image-error');
function showScreen() {
  const key = Object.hasOwn(screens, location.hash.slice(1)) ? location.hash.slice(1) : 'claim';
  const screen = screens[key];
  document.title = `${screen.title} · Inspro design review`;
  for (const [id, value] of Object.entries({'screen-title':screen.title,'screen-context':screen.context,'screen-summary':screen.summary,'review-role':screen.role,'sheet-type':screen.wireframe?'Companion wireframe':'Annotated screen'})) document.getElementById(id).textContent = value;
  error.hidden = true;
  image.alt = screen.alt;
  image.src = screen.image;
  image.width = screen.wireframe ? 1152 : 1536;
  image.height = screen.wireframe ? 768 : 1024;
  document.querySelector('#open-image').href = screen.image;
  viewport.classList.remove('is-zoomed');
  viewport.scrollTo(0,0);
  zoom.setAttribute('aria-pressed','false');
  zoom.textContent = 'Zoom to read';
  document.querySelectorAll('[data-screen]').forEach(link => {
    if (link.dataset.screen === key) link.setAttribute('aria-current','page');
    else link.removeAttribute('aria-current');
  });
  const notes = screen.notes.map(([title,text]) => {
    const li = document.createElement('li');
    const strong = document.createElement('strong');
    strong.textContent = title;
    li.append(strong,document.createTextNode(text));
    return li;
  });
  document.querySelector('#annotations').replaceChildren(...notes);
  document.querySelector('#screen-announcement').textContent = `Showing ${screen.title}. ${screen.wireframe?'Companion wireframe.':'Annotated screen.'}`;
}
zoom.addEventListener('click',() => {
  const enlarged = viewport.classList.toggle('is-zoomed');
  zoom.setAttribute('aria-pressed',String(enlarged));
  zoom.textContent = enlarged ? 'Fit to page' : 'Zoom to read';
  if (!enlarged) viewport.scrollTo(0,0);
});
image.addEventListener('error',() => {error.hidden = false;});
image.addEventListener('load',() => {error.hidden = true;});
window.addEventListener('hashchange',showScreen);
showScreen();
