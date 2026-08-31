import { Plus, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { InfoHint } from "@/components/ui/tooltip";
import type { AttributeSchema, RuleNode } from "@/types";
import { cn } from "@/lib/cn";

interface Props {
  rule: RuleNode;
  schema: AttributeSchema[];
  onChange: (rule: RuleNode) => void;
}

const COMPARISON_OPS = ["=", "!=", ">=", "<=", ">", "<", "between", "in", "not_in"] as const;
type ComparisonOp = (typeof COMPARISON_OPS)[number];

type Node = RuleNode;

function blankCondition(): Node {
  return { "=": ["", ""] };
}

/**
 * Add a required condition without replacing the broker's current rule.
 *
 * A single comparison cannot contain siblings in the rule AST, so promote it
 * to an AND group first. Existing AND groups can be extended in place. Keeping
 * this transformation here makes the UI action deterministic and testable.
 */
export function addRequiredCondition(
  rule: RuleNode,
  schema: AttributeSchema[],
): RuleNode {
  if (schema.length === 0) return rule;
  const condition = blankCondition();
  if (!rule) return condition;

  const keys = Object.keys(rule);
  if (keys.length === 1 && keys[0] === "and") {
    const children = (rule as Record<string, unknown>).and;
    if (Array.isArray(children)) {
      return { and: [...children, condition] };
    }
  }

  return { and: [rule, condition] };
}

export function RuleBuilder({ rule, schema, onChange }: Props) {
  if (!rule) {
    return (
      <div className="rounded-md border border-dashed border-border bg-muted/30 p-4 flex items-center justify-between">
        <span className="text-sm text-muted-foreground">No rule yet</span>
        <div className="flex gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() => onChange(addRequiredCondition(null, schema))}
            disabled={schema.length === 0}
          >
            <Plus className="size-3.5" /> Add condition
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => onChange({ and: [] })}
          >
            <Plus className="size-3.5" /> AND group
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => onChange({ or: [] })}
          >
            <Plus className="size-3.5" /> OR group
          </Button>
        </div>
      </div>
    );
  }
  return (
    <div className="space-y-3">
      <NodeView node={rule} schema={schema} onChange={onChange} depth={0} />
      {!isAndGroupNode(rule) && (
        <div className="flex flex-wrap items-center justify-between gap-2 px-1">
          <p className="text-xs text-muted-foreground">
            Add another required employee attribute to narrow this category.
          </p>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => onChange(addRequiredCondition(rule, schema))}
            disabled={schema.length === 0}
            title={
              schema.length === 0
                ? "No matchable employee fields are available"
                : "Add another required condition (AND)"
            }
          >
            <Plus className="size-3.5" /> Add required condition
          </Button>
        </div>
      )}
    </div>
  );
}

function isAndGroupNode(node: Node): boolean {
  if (!node) return false;
  const keys = Object.keys(node);
  return keys.length === 1 && keys[0] === "and";
}

function NodeView({
  node,
  schema,
  onChange,
  depth,
}: {
  node: Node;
  schema: AttributeSchema[];
  onChange: (n: Node) => void;
  depth: number;
}) {
  if (!node) return null;
  const key = Object.keys(node)[0];
  const args = (node as Record<string, unknown>)[key];

  if (key === "and" || key === "or") {
    const children = (args as Node[]) ?? [];
    const addConditionLabel =
      key === "and" ? "Add required condition" : "Add alternative";
    return (
      <div
        className={cn(
          "rounded-md border border-border bg-card",
          depth > 0 && "bg-muted/30",
        )}
      >
        <div className="px-3 py-2 border-b border-border flex items-center justify-between">
          <Badge variant={key === "and" ? "primary" : "warn"}>{key.toUpperCase()}</Badge>
          <div className="flex gap-1">
            <Button
              size="sm"
              variant="ghost"
              onClick={() =>
                onChange({
                  [key]: [
                    ...children,
                    blankCondition(),
                  ],
                } as Node)
              }
              disabled={schema.length === 0}
            >
              <Plus className="size-3.5" /> {addConditionLabel}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() =>
                onChange({ [key]: [...children, { and: [] }] } as Node)
              }
            >
              <Plus className="size-3.5" /> Add AND group
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() =>
                onChange({ [key]: [...children, { or: [] }] } as Node)
              }
            >
              <Plus className="size-3.5" /> Add OR group
            </Button>
          </div>
        </div>
        <div className="p-3 space-y-2">
          {children.length === 0 && (
            <div className="text-xs text-muted-foreground italic">Empty group</div>
          )}
          {children.map((child, idx) => (
            <div key={idx} className="flex items-start gap-2">
              <div className="flex-1">
                <NodeView
                  node={child}
                  schema={schema}
                  onChange={(updated) => {
                    const next = [...children];
                    next[idx] = updated;
                    onChange({ [key]: next } as Node);
                  }}
                  depth={depth + 1}
                />
              </div>
              <Button
                size="icon"
                variant="ghost"
                aria-label="Remove condition"
                title="Remove condition"
                onClick={() => {
                  const next = children.filter((_, i) => i !== idx);
                  onChange({ [key]: next } as Node);
                }}
              >
                <Trash2 className="size-3.5 text-muted-foreground" />
              </Button>
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (key === "not") {
    return (
      <div className="rounded-md border border-border bg-card">
        <div className="px-3 py-2 border-b border-border flex items-center justify-between">
          <Badge variant="error">NOT</Badge>
        </div>
        <div className="p-3">
          <NodeView
            node={args as Node}
            schema={schema}
            onChange={(updated) => onChange({ not: updated } as Node)}
            depth={depth + 1}
          />
        </div>
      </div>
    );
  }

  // Comparison
  const safeArgs = Array.isArray(args) ? (args as unknown[]) : [];
  return (
    <ComparisonRow
      op={key as ComparisonOp}
      args={safeArgs}
      schema={schema}
      onChange={(newKey, newArgs) =>
        onChange({ [newKey]: newArgs } as Node)
      }
    />
  );
}

function ComparisonRow({
  op,
  args,
  schema,
  onChange,
}: {
  op: ComparisonOp;
  args: unknown[];
  schema: AttributeSchema[];
  onChange: (op: ComparisonOp, args: unknown[]) => void;
}) {
  const [attr = "", ...rest] = args ?? [];
  const attrSchema = schema.find((a) => a.attribute_id === attr);
  return (
    <div className="rounded-md border border-border bg-card p-3 flex flex-wrap items-center gap-2">
      <Select
        value={String(attr)}
        onValueChange={(v) => onChange(op, [v, ...rest])}
      >
        <SelectTrigger className="w-[180px]" aria-label="Employee attribute">
          <SelectValue placeholder="Attribute" />
        </SelectTrigger>
        <SelectContent>
          {schema.map((a) => (
            <SelectItem key={a.attribute_id} value={a.attribute_id}>
              {a.display_name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select value={op} onValueChange={(v) => onChange(v as ComparisonOp, args)}>
        <SelectTrigger className="w-[120px]" aria-label="Comparison operator">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {COMPARISON_OPS.map((o) => (
            <SelectItem key={o} value={o}>
              {o}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <InfoHint>
        Comparison operator. <code>between</code> takes a min and max;{" "}
        <code>in</code> / <code>not_in</code> match any of a comma-separated
        list.
      </InfoHint>

      {op === "between" ? (
        <>
          <ValueInput
            value={rest[0]}
            attrSchema={attrSchema}
            placeholder="min"
            ariaLabel={`${attrSchema?.display_name ?? "Attribute"} minimum`}
            onChange={(v) => onChange(op, [attr, v, rest[1]])}
          />
          <span className="text-muted-foreground text-xs">to</span>
          <ValueInput
            value={rest[1]}
            attrSchema={attrSchema}
            placeholder="max"
            ariaLabel={`${attrSchema?.display_name ?? "Attribute"} maximum`}
            onChange={(v) => onChange(op, [attr, rest[0], v])}
          />
        </>
      ) : op === "in" || op === "not_in" ? (
        <Input
          className="w-[260px]"
          placeholder="comma-separated (e.g. WP, SP)"
          aria-label={`${attrSchema?.display_name ?? "Attribute"} values`}
          value={
            Array.isArray(rest[0])
              ? (rest[0] as unknown[]).join(", ")
              : ""
          }
          onChange={(e) =>
            onChange(op, [
              attr,
              e.target.value
                .split(",")
                .map((s) => s.trim())
                .filter(Boolean),
            ])
          }
        />
      ) : (
        <ValueInput
          value={rest[0]}
          attrSchema={attrSchema}
          ariaLabel={`${attrSchema?.display_name ?? "Attribute"} value`}
          onChange={(v) => onChange(op, [attr, v])}
        />
      )}
    </div>
  );
}

function ValueInput({
  value,
  attrSchema,
  placeholder,
  ariaLabel,
  onChange,
}: {
  value: unknown;
  attrSchema?: AttributeSchema;
  placeholder?: string;
  ariaLabel: string;
  onChange: (value: unknown) => void;
}) {
  if (attrSchema?.data_type === "enum" && attrSchema.enum_values) {
    return (
      <Select
        value={typeof value === "string" ? value : ""}
        onValueChange={onChange}
      >
        <SelectTrigger className="w-[180px]" aria-label={ariaLabel}>
          <SelectValue placeholder="Value" />
        </SelectTrigger>
        <SelectContent>
          {attrSchema.enum_values.map((v) => (
            <SelectItem key={v} value={v}>
              {v}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    );
  }
  const isNumeric =
    attrSchema?.data_type === "integer" || attrSchema?.data_type === "decimal";
  return (
    <Input
      className="w-[140px]"
      type={isNumeric ? "number" : "text"}
      placeholder={placeholder ?? "value"}
      aria-label={ariaLabel}
      value={typeof value === "string" || typeof value === "number" ? value : ""}
      onChange={(e) => {
        const v = e.target.value;
        if (isNumeric) {
          onChange(v === "" ? null : Number(v));
        } else {
          onChange(v);
        }
      }}
    />
  );
}
