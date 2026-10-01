/** Broker setup for one period's e-form.
 *
 * Products, plans, family rates and age limits are NOT edited here — the form
 * reads them from the placement slip and benefit settings, which is what makes
 * it fill itself in per company. What is set here is only what a slip cannot
 * say: the wording, the documents members must read, how the premium is shared
 * between company and employee, and "X only with Y" rules. Until it is saved
 * the form runs on generated defaults. */
import { FileUp, Loader2, Plus, Trash2 } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";
import {
  type FormConfig,
  type FormSettings,
  downloadBrokerFormDocument,
  uploadFormDocument,
  useFormConfig,
  useSaveFormConfig,
} from "@/api/enrollmentForms";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/cn";
import { formatError } from "@/lib/errors";

const textareaClass =
  "min-h-20 w-full rounded-md border border-input bg-card px-3 py-2 text-sm text-foreground " +
  "shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40";

function newId(prefix: string, taken: string[]): string {
  let i = taken.length + 1;
  while (taken.includes(`${prefix}_${i}`)) i += 1;
  return `${prefix}_${i}`;
}

function pct(value: string): number | null {
  if (!value.trim()) return null;
  const n = Number(value);
  return Number.isFinite(n) ? Math.min(100, Math.max(0, n)) : null;
}

function Section({ title, hint, children }: { title: string; hint: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3 border-t border-border pt-4 first:border-t-0 first:pt-0">
      <div>
        <h3 className="text-sm font-semibold text-foreground">{title}</h3>
        <p className="mt-0.5 text-sm text-muted-foreground">{hint}</p>
      </div>
      {children}
    </section>
  );
}

export function FormSetupCard({ windowId, readOnly }: { windowId: string; readOnly: boolean }) {
  const config = useFormConfig(windowId);
  if (config.isLoading) return <Skeleton className="h-64 rounded-lg" />;
  if (config.isError || !config.data) {
    return (
      <Card className="p-5 text-sm text-muted-foreground" role="alert">
        {formatError(config.error)}
      </Card>
    );
  }
  return <FormSetupEditor key={`${windowId}-${config.data.updated_at}`} config={config.data} readOnly={readOnly} />;
}

function FormSetupEditor({ config, readOnly }: { config: FormConfig; readOnly: boolean }) {
  const [s, setS] = useState<FormSettings>(config.settings);
  const [dirty, setDirty] = useState(false);
  const [uploading, setUploading] = useState(false);
  const save = useSaveFormConfig(config.window_id);
  const fileInput = useRef<HTMLInputElement>(null);

  const update = (patch: Partial<FormSettings>) => {
    setS((prev) => ({ ...prev, ...patch }));
    setDirty(true);
  };
  const products = config.products;
  const spouseMax = config.age_limits.spouse?.max;
  const childMax = config.age_limits.child?.max;

  async function onSave() {
    try {
      await save.mutateAsync(s);
      setDirty(false);
      toast.success("Form setup saved");
    } catch (e) {
      toast.error(formatError(e));
    }
  }

  async function onUpload(file: File) {
    setUploading(true);
    try {
      const doc = await uploadFormDocument(config.window_id, file);
      update({
        documents: [
          ...s.documents,
          {
            id: newId("doc", s.documents.map((d) => d.id)),
            label: file.name.replace(/\.[^.]+$/, ""),
            url: null,
            document_id: doc.document_id,
          },
        ],
      });
    } catch (e) {
      toast.error(formatError(e));
    } finally {
      setUploading(false);
    }
  }

  return (
    <Card className="space-y-5 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-foreground">Form setup</h2>
          <p className="mt-0.5 max-w-2xl text-sm text-muted-foreground">
            Plans, family rates and who is eligible come from this year&rsquo;s placement slip and
            benefit settings. Set the wording and the employee&rsquo;s share of each premium here.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {config.is_default && !dirty && <Badge variant="info">Using defaults</Badge>}
          {!readOnly && (
            <Button disabled={!dirty || save.isPending} onClick={() => void onSave()}>
              {save.isPending && <Loader2 className="size-4 animate-spin" />}
              Save setup
            </Button>
          )}
        </div>
      </div>

      <fieldset disabled={readOnly} className="space-y-5">
        <Section title="Heading and contact" hint="Printed at the top and bottom of the form and its PDF.">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="ef-title">Form title</Label>
              <Input id="ef-title" value={s.title} maxLength={160} onChange={(e) => update({ title: e.target.value })} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="ef-help">Helpline</Label>
              <Input
                id="ef-help"
                value={s.helpline ?? ""}
                maxLength={300}
                placeholder="Questions? Call 6448 7707 or email helpdesk@…"
                onChange={(e) => update({ helpline: e.target.value || null })}
              />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ef-note">After-submission note</Label>
            <Input
              id="ef-note"
              value={s.submission_note ?? ""}
              maxLength={1000}
              onChange={(e) => update({ submission_note: e.target.value || null })}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ef-intro">Introduction (optional)</Label>
            <textarea
              id="ef-intro"
              className={textareaClass}
              value={s.intro ?? ""}
              maxLength={3000}
              placeholder="Leave blank to generate it: which plans are automatic, and when changes can be made."
              onChange={(e) => update({ intro: e.target.value || null })}
            />
          </div>
        </Section>

        <Section
          title="Employee's share of the premium"
          hint="Members see each plan's premium and their share. Leave every box blank to show no premium for that product. Upgrade % applies when the company pays the member's own cover: the member pays that share of the extra for a higher plan."
        >
          <div className="overflow-x-auto">
            <table className="w-full min-w-[40rem] text-sm">
              <thead>
                <tr className="text-left text-xs text-muted-foreground">
                  <th className="py-1.5 pr-3 font-medium">Product</th>
                  <th className="py-1.5 pr-3 font-medium">Participation</th>
                  <th className="py-1.5 pr-3 font-medium">Employee cover %</th>
                  <th className="py-1.5 pr-3 font-medium">Family cover %</th>
                  <th className="py-1.5 font-medium">Upgrade %</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {products.map((p) => {
                  const share = s.contributions[p.product_code] ?? {
                    employee_pct: null,
                    dependant_pct: null,
                    upgrade_pct: null,
                  };
                  const setShare = (patch: Partial<typeof share>) => {
                    const next = { ...share, ...patch };
                    const rest = { ...s.contributions };
                    if (
                      next.employee_pct === null &&
                      next.dependant_pct === null &&
                      (next.upgrade_pct ?? null) === null
                    ) {
                      delete rest[p.product_code];
                    } else {
                      rest[p.product_code] = next;
                    }
                    update({ contributions: rest });
                  };
                  return (
                    <tr key={p.product_code}>
                      <td className="py-2 pr-3 text-foreground">{p.product_name ?? p.product_code}</td>
                      <td className="py-2 pr-3 capitalize text-muted-foreground">{p.participation ?? "—"}</td>
                      <td className="py-2 pr-3">
                        <Input
                          type="number" min={0} max={100} step="any" className="h-8 w-24"
                          aria-label={`${p.product_code} employee share`}
                          value={share.employee_pct ?? ""}
                          onChange={(e) => setShare({ employee_pct: pct(e.target.value) })}
                        />
                      </td>
                      <td className="py-2 pr-3">
                        <Input
                          type="number" min={0} max={100} step="any" className="h-8 w-24"
                          aria-label={`${p.product_code} family share`}
                          disabled={!p.has_dependant_cover}
                          value={share.dependant_pct ?? ""}
                          onChange={(e) => setShare({ dependant_pct: pct(e.target.value) })}
                        />
                      </td>
                      <td className="py-2">
                        <Input
                          type="number" min={0} max={100} step="any" className="h-8 w-24"
                          aria-label={`${p.product_code} upgrade share`}
                          disabled={!p.has_upgrades || share.employee_pct !== null}
                          value={share.upgrade_pct ?? ""}
                          onChange={(e) => setShare({ upgrade_pct: pct(e.target.value) })}
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Section>

        <Section title="Taken together" hint="Cover on the first product requires cover on the second (e.g. major medical only with hospital & surgical).">
          {s.rules.map((rule, i) => (
            <div key={i} className="flex flex-wrap items-center gap-2 text-sm">
              <NativeSelect
                aria-label="Product"
                value={rule.product_code}
                onChange={(e) => update({ rules: s.rules.map((r, j) => (j === i ? { ...r, product_code: e.target.value } : r)) })}
              >
                {products.map((p) => <option key={p.product_code} value={p.product_code}>{p.product_code}</option>)}
              </NativeSelect>
              <span className="text-muted-foreground">only with</span>
              <NativeSelect
                aria-label="Required product"
                value={rule.requires_product_code}
                onChange={(e) => update({ rules: s.rules.map((r, j) => (j === i ? { ...r, requires_product_code: e.target.value } : r)) })}
              >
                {products.map((p) => <option key={p.product_code} value={p.product_code}>{p.product_code}</option>)}
              </NativeSelect>
              <Button variant="ghost" size="sm" aria-label="Remove rule" onClick={() => update({ rules: s.rules.filter((_, j) => j !== i) })}>
                <Trash2 className="size-4" />
              </Button>
            </div>
          ))}
          {products.length > 1 && (
            <Button
              variant="outline" size="sm"
              onClick={() => update({ rules: [...s.rules, { product_code: products[0].product_code, requires_product_code: products[1].product_code }] })}
            >
              <Plus className="size-4" /> Add rule
            </Button>
          )}
        </Section>

        <Section title="Documents to read" hint="MAS requires members to read the health insurance guide, product summary and benefit schedule before voluntary cover.">
          {s.documents.map((doc, i) => (
            <div key={doc.id} className="flex flex-col gap-2 sm:flex-row sm:items-center">
              <Input
                aria-label="Document name" value={doc.label} maxLength={160} className="sm:max-w-xs"
                onChange={(e) => update({ documents: s.documents.map((d, j) => (j === i ? { ...d, label: e.target.value } : d)) })}
              />
              {doc.document_id ? (
                <Button
                  variant="link" size="sm" className="justify-start px-0"
                  onClick={() => void downloadBrokerFormDocument(config.window_id, doc.document_id!, doc.label).catch((e) => toast.error(formatError(e)))}
                >
                  Uploaded file
                </Button>
              ) : (
                <Input
                  aria-label="Link" value={doc.url ?? ""} placeholder="https://…"
                  onChange={(e) => update({ documents: s.documents.map((d, j) => (j === i ? { ...d, url: e.target.value || null } : d)) })}
                />
              )}
              <Button variant="ghost" size="sm" aria-label="Remove document" onClick={() => update({ documents: s.documents.filter((_, j) => j !== i) })}>
                <Trash2 className="size-4" />
              </Button>
            </div>
          ))}
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline" size="sm"
              onClick={() => update({ documents: [...s.documents, { id: newId("doc", s.documents.map((d) => d.id)), label: "", url: null, document_id: null }] })}
            >
              <Plus className="size-4" /> Add link
            </Button>
            <input
              ref={fileInput} type="file" accept=".pdf,.png,.jpg,.jpeg" className="hidden"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) void onUpload(f); e.target.value = ""; }}
            />
            <Button variant="outline" size="sm" disabled={uploading} onClick={() => fileInput.current?.click()}>
              {uploading ? <Loader2 className="size-4 animate-spin" /> : <FileUp className="size-4" />} Upload file
            </Button>
          </div>
        </Section>

        <Section title="Declarations" hint="Each one is a box the member must tick before signing. Family-only ones appear when someone in the family is named.">
          {s.clauses.map((clause, i) => (
            <div key={clause.id} className="flex flex-col gap-2 sm:flex-row sm:items-start">
              <textarea
                aria-label={`Declaration ${i + 1}`} className={cn(textareaClass, "min-h-14")} value={clause.text} maxLength={1500}
                onChange={(e) => update({ clauses: s.clauses.map((c, j) => (j === i ? { ...c, text: e.target.value } : c)) })}
              />
              <div className="flex items-center gap-2">
                <NativeSelect
                  aria-label="Shown when"
                  value={clause.applies_to}
                  onChange={(e) => update({ clauses: s.clauses.map((c, j) => (j === i ? { ...c, applies_to: e.target.value as "all" | "dependants" } : c)) })}
                >
                  <option value="all">Always</option>
                  <option value="dependants">With family</option>
                </NativeSelect>
                <Button variant="ghost" size="sm" aria-label="Remove declaration" onClick={() => update({ clauses: s.clauses.filter((_, j) => j !== i) })}>
                  <Trash2 className="size-4" />
                </Button>
              </div>
            </div>
          ))}
          <Button
            variant="outline" size="sm"
            onClick={() => update({ clauses: [...s.clauses, { id: newId("clause", s.clauses.map((c) => c.id)), text: "", applies_to: "all" }] })}
          >
            <Plus className="size-4" /> Add declaration
          </Button>
        </Section>

        <Section
          title="Family eligibility notes"
          hint={`Age limits come from benefit settings${spouseMax || childMax ? ` (spouse up to ${spouseMax ?? "—"}, children up to ${childMax ?? "—"}, next birthday)` : ""} and are printed automatically. Add any other conditions, one per line.`}
        >
          <textarea
            aria-label="Eligibility notes" className={textareaClass}
            value={s.eligibility_notes.join("\n")}
            onChange={(e) => update({ eligibility_notes: e.target.value.split("\n") })}
            onBlur={() => update({ eligibility_notes: s.eligibility_notes.map((n) => n.trim()).filter(Boolean) })}
          />
        </Section>
      </fieldset>
    </Card>
  );
}
