import { create } from "zustand";

// Explicitly a local UI review. These records never go to a claim/provider API,
// browser storage, or a production approval workflow.
export const AI_REVIEW_ENABLED = import.meta.env.DEV;
export type ReviewState =
  "Action required" | "Awaiting review" | "Not assessed" | "Reviewed";
export interface AIUse {
  id: string;
  name: string;
  purpose: string;
  owner: string;
  deployment: "Active" | "Manual handling";
  review: ReviewState;
  nextAction: string;
  nextActionId?: string;
  risk: string;
  control: string;
}
export interface Evidence {
  id: string;
  title: string;
  version: string;
  owner: string;
  reviewer: string;
  status:
    "Draft" | "Awaiting approval" | "Approved" | "Not recorded" | "Review due";
  reference: string;
  history: string[];
}
export interface ReviewAction {
  id: string;
  title: string;
  owner: string;
  due: string;
  status: "Open" | "In progress" | "Complete";
}
export interface PublicDecision {
  outcome: "Approve" | "Reject" | "Request information";
  amount: string;
  explanation: string;
  reason: string;
}
export interface DecisionSupport {
  internalNote: string;
  reference: string;
}

function initialState() {
  return {
    revision: 0,
    uses: [
      {
        id: "claim-review",
        name: "Claim review",
        purpose:
          "Checks claim documents and highlights concerns. A claims assessor makes the decision.",
        owner: "Claims lead",
        deployment: "Active",
        review: "Action required",
        nextAction: "Validate the review dataset",
        nextActionId: "validation",
        risk: "An unsupported clean result could lead to an incorrect claim decision.",
        control:
          "Assessor checks source evidence; manual review remains available.",
      },
      {
        id: "claim-autofill",
        name: "Claim autofill",
        purpose:
          "Suggests claim details from documents for the member to check and edit.",
        owner: "Product lead",
        deployment: "Active",
        review: "Awaiting review",
        nextAction: "Review member disclosure",
        nextActionId: "disclosure",
        risk: "An incorrectly extracted amount or date may go unnoticed.",
        control:
          "Members confirm editable details before submitting their claim.",
      },
      {
        id: "slip-extraction",
        name: "Slip extraction",
        purpose:
          "Extracts draft benefit information from placement slips for broker review.",
        owner: "Unassigned",
        deployment: "Active",
        review: "Not assessed",
        nextAction: "Assign an owner",
        risk: "A benefit limit could be imported incorrectly.",
        control: "Broker compares extracted benefits with the source document.",
      },
    ] as AIUse[],
    actions: [
      {
        id: "validation",
        title: "Validate the review dataset",
        owner: "AI lead",
        due: "15 Oct 2026",
        status: "Open",
      },
      {
        id: "disclosure",
        title: "Review member disclosure",
        owner: "Product lead",
        due: "12 Oct 2026",
        status: "In progress",
      },
      {
        id: "supplier",
        title: "Verify provider retention and training-use terms",
        owner: "Privacy owner",
        due: "15 Oct 2026",
        status: "Open",
      },
    ] as ReviewAction[],
    evidence: [
      {
        id: "policy",
        title: "AI policy",
        version: "Draft 1",
        owner: "Firm administrator",
        reviewer: "",
        status: "Draft",
        reference: "AI-policy-draft.pdf",
        history: ["Draft 1 created for the local review."],
      },
      {
        id: "training",
        title: "Assessor training",
        version: "Not recorded",
        owner: "Claims lead",
        reviewer: "",
        status: "Not recorded",
        reference: "",
        history: [],
      },
      {
        id: "processing",
        title: "Data processing agreement",
        version: "v1",
        owner: "Privacy owner",
        reviewer: "",
        status: "Review due",
        reference: "",
        history: [],
      },
    ] as Evidence[],
    publicDecision: {
      outcome: "Approve",
      amount: "120.00",
      explanation:
        "The consultation is covered. The S$30 non-covered item is excluded under your policy.",
      reason: "Excluded expense",
    } as PublicDecision,
    decisionSupport: {
      internalNote: "",
      reference: "Policy schedule · Excluded expenses",
    } as DecisionSupport,
    decisionRecorded: false,
    reconsideration: "",
    evaluationAttached: false,
    reviewer: "",
    approvalReason: "",
    release: "Blocked" as
      "Blocked" | "Awaiting approval" | "Approved" | "Active",
    supplierReference: "",
    supplierNotes: "",
  };
}
type State = ReturnType<typeof initialState>;
interface ReviewStore extends State {
  updateUse: (id: string, changes: Partial<AIUse>) => void;
  addUse: (record: AIUse) => void;
  updateAction: (id: string, changes: Partial<ReviewAction>) => void;
  updateEvidence: (id: string, changes: Partial<Evidence>) => void;
  recordDecision: (decision: PublicDecision, support: DecisionSupport) => void;
  requestReview: (message: string) => void;
  patch: (changes: Partial<State>) => void;
  reset: () => void;
}
export const useAIReview = create<ReviewStore>((set) => ({
  ...initialState(),
  updateUse: (id, changes) =>
    set((s) => ({
      uses: s.uses.map((u) => (u.id === id ? { ...u, ...changes } : u)),
    })),
  addUse: (record) =>
    set((s) => {
      const nextActionId = `assess-${record.id}`;
      return {
        uses: [...s.uses, { ...record, nextActionId }],
        actions: [
          ...s.actions,
          {
            id: nextActionId,
            title: record.nextAction,
            owner: record.owner,
            due: "Not scheduled",
            status: "Open",
          },
        ],
      };
    }),
  updateAction: (id, changes) =>
    set((s) => ({
      actions: s.actions.map((a) => (a.id === id ? { ...a, ...changes } : a)),
    })),
  updateEvidence: (id, changes) =>
    set((s) => ({
      evidence: s.evidence.map((e) => (e.id === id ? { ...e, ...changes } : e)),
    })),
  // Save both projections atomically in memory. Only the public projection is
  // consumed by the member journey; neither projection is persisted or sent.
  recordDecision: (decision, decisionSupport) =>
    set({
      publicDecision: {
        outcome: decision.outcome,
        amount: decision.amount,
        reason: decision.reason,
        explanation: decision.explanation,
      },
      decisionSupport: { ...decisionSupport },
      decisionRecorded: true,
      reconsideration: "",
    }),
  requestReview: (reconsideration) => set({ reconsideration }),
  patch: (changes) => set(changes),
  reset: () => set((s) => ({ ...initialState(), revision: s.revision + 1 })),
}));
