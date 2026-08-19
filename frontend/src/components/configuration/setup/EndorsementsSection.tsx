import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { EndorsementAnswer } from "@/types";

interface Props {
  endorsements: EndorsementAnswer[];
  onChange: (endorsements: EndorsementAnswer[]) => void;
}

const blankEndorsement = (): EndorsementAnswer => ({
  source_cell: null,
  source_row: null,
  item_no: null,
  year: "",
  label: "",
  name: "",
  content: "",
  comment: "",
  author: null,
});

function updateAt(
  endorsements: EndorsementAnswer[],
  index: number,
  patch: Partial<EndorsementAnswer>,
) {
  return endorsements.map((endorsement, i) =>
    i === index ? { ...endorsement, ...patch } : endorsement,
  );
}

export function EndorsementsSection({ endorsements, onChange }: Props) {
  const add = () => onChange([...endorsements, blankEndorsement()]);
  const remove = (index: number) =>
    onChange(endorsements.filter((_, i) => i !== index));

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-3">
        <Label className="text-2xs uppercase tracking-wider text-muted-foreground">
          Endorsements · {endorsements.length}
        </Label>
        <Button type="button" variant="outline" size="sm" onClick={add}>
          <Plus className="size-3.5" /> Add endorsement
        </Button>
      </div>

      {endorsements.length === 0 ? (
        <div className="rounded-md border border-dashed border-border bg-muted/30 px-4 py-6 text-sm text-muted-foreground">
          No year-labelled endorsements were extracted for this product.
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {endorsements.map((endorsement, index) => (
            <section
              key={`${endorsement.source_cell ?? "manual"}-${index}`}
              className="rounded-md border border-border bg-card p-3 shadow-sm"
            >
              <div className="mb-3 flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-sm font-medium text-foreground">
                    {endorsement.name || "Untitled endorsement"}
                  </div>
                  <div className="mt-0.5 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
                    {endorsement.source_cell ? (
                      <span>{endorsement.source_cell}</span>
                    ) : null}
                    {endorsement.author ? <span>{endorsement.author}</span> : null}
                  </div>
                </div>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  onClick={() => remove(index)}
                  aria-label={`Remove endorsement ${endorsement.name || index + 1}`}
                  title="Remove endorsement"
                >
                  <Trash2 className="size-4 text-error" />
                </Button>
              </div>

              <div className="grid grid-cols-1 gap-3 md:grid-cols-[8rem_1fr]">
                <div className="flex flex-col gap-1.5">
                  <Label className="text-2xs uppercase tracking-wider text-muted-foreground">
                    Year
                  </Label>
                  <Input
                    value={endorsement.year}
                    onChange={(e) =>
                      onChange(updateAt(endorsements, index, { year: e.target.value }))
                    }
                    inputMode="numeric"
                  />
                </div>
                <div className="flex flex-col gap-1.5">
                  <Label className="text-2xs uppercase tracking-wider text-muted-foreground">
                    Endorsement label / no.
                  </Label>
                  <Input
                    value={endorsement.label}
                    onChange={(e) =>
                      onChange(updateAt(endorsements, index, { label: e.target.value }))
                    }
                  />
                </div>
              </div>

              <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-[8rem_1fr]">
                <div className="flex flex-col gap-1.5">
                  <Label className="text-2xs uppercase tracking-wider text-muted-foreground">
                    Item no.
                  </Label>
                  <Input
                    value={endorsement.item_no ?? ""}
                    onChange={(e) =>
                      onChange(
                        updateAt(endorsements, index, {
                          item_no: e.target.value || null,
                        }),
                      )
                    }
                  />
                </div>
                <div className="flex flex-col gap-1.5">
                  <Label className="text-2xs uppercase tracking-wider text-muted-foreground">
                    Endorsement name / subject
                  </Label>
                  <Input
                    value={endorsement.name}
                    onChange={(e) =>
                      onChange(updateAt(endorsements, index, { name: e.target.value }))
                    }
                  />
                </div>
              </div>

              <div className="mt-3 grid grid-cols-1 gap-3 lg:grid-cols-2">
                <div className="flex flex-col gap-1.5">
                  <Label className="text-2xs uppercase tracking-wider text-muted-foreground">
                    Endorsement content
                  </Label>
                  <textarea
                    value={endorsement.content}
                    onChange={(e) =>
                      onChange(
                        updateAt(endorsements, index, { content: e.target.value }),
                      )
                    }
                    rows={7}
                    className="min-h-36 resize-y rounded-md border border-input bg-card px-3 py-2 text-sm text-foreground shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
                  />
                </div>
                <div className="flex flex-col gap-1.5">
                  <Label className="text-2xs uppercase tracking-wider text-muted-foreground">
                    Source comment
                  </Label>
                  <textarea
                    value={endorsement.comment ?? ""}
                    onChange={(e) =>
                      onChange(
                        updateAt(endorsements, index, { comment: e.target.value }),
                      )
                    }
                    rows={7}
                    className="min-h-36 resize-y rounded-md border border-input bg-card px-3 py-2 text-sm text-foreground shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
                  />
                </div>
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
