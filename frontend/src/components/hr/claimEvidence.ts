/** Match the server's generic receipt and specifically tagged evidence rules. */
export function documentsForSlot<T extends { doc_type: string | null }>(
  documents: T[],
  key: string,
): T[] {
  return key === "invoice_receipt"
    ? documents
    : documents.filter((document) => document.doc_type === key);
}
