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

export function RuleBuilder({ rule, schema, onChange }: Props) {
  if (!rule) {
    return (
      <div className="rounded-md border border-dashed border-border bg-muted/30 p-4 flex items-center justify-between">
        <span className="text-sm text-muted-foreground">No rule yet</span>
        <div className="flex gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() =>
              onChange({ "=": [schema[0]?.attribute_id ?? "grade", ""] })
            }
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
    </div>
  );
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
                    { "=": [schema[0]?.attribute_id ?? "grade", ""] },
                  ],
                } as Node)
              }
            >
              <Plus className="size-3.5" /> Condition
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() =>
                onChange({ [key]: [...children, { and: [] }] } as Node)
              }
            >
              <Plus className="size-3.5" /> AND group
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() =>
                onChange({ [key]: [...children, { or: [] }] } as Node)
              }
            >
              <Plus className="size-3.5" /> OR group
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
        <SelectTrigger className="w-[180px]">
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
        <SelectTrigger className="w-[120px]">
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
            onChange={(v) => onChange(op, [attr, v, rest[1]])}
          />
          <span className="text-muted-foreground text-xs">to</span>
          <ValueInput
            value={rest[1]}
            attrSchema={attrSchema}
            placeholder="max"
            onChange={(v) => onChange(op, [attr, rest[0], v])}
          />
        </>
      ) : op === "in" || op === "not_in" ? (
        <Input
          className="w-[260px]"
          placeholder="comma-separated (e.g. WP, SP)"
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
  onChange,
}: {
  value: unknown;
  attrSchema?: AttributeSchema;
  placeholder?: string;
  onChange: (value: unknown) => void;
}) {
  if (attrSchema?.data_type === "enum" && attrSchema.enum_values) {
    return (
      <Select
        value={typeof value === "string" ? value : ""}
        onValueChange={onChange}
      >
        <SelectTrigger className="w-[180px]">
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
