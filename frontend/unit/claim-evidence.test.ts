import assert from "node:assert/strict";
import test from "node:test";
import { documentsForSlot } from "../src/components/hr/claimEvidence.ts";

test("a generic receipt accepts legacy and specifically tagged attachments", () => {
  const legacy = { id: "legacy", doc_type: null };
  const tagged = { id: "invoice", doc_type: "itemised_tax_invoice" };
  assert.deepEqual(documentsForSlot([legacy], "invoice_receipt"), [legacy]);
  assert.deepEqual(documentsForSlot([tagged], "invoice_receipt"), [tagged]);
  assert.deepEqual(documentsForSlot([], "invoice_receipt"), []);
});

test("a specific hospital requirement accepts only its matching tag", () => {
  const receipt = { id: "receipt", doc_type: "invoice_receipt" };
  const legacy = { id: "legacy", doc_type: null };
  const discharge = { id: "discharge", doc_type: "discharge_summary" };
  assert.deepEqual(documentsForSlot([receipt, legacy], "discharge_summary"), []);
  assert.deepEqual(documentsForSlot([receipt, legacy, discharge], "discharge_summary"), [discharge]);
});
