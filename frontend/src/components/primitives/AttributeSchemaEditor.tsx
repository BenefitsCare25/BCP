import { Pencil, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { AttributeSchema } from "@/types";
import { Globe, Lock } from "lucide-react";

interface Props {
  attributes: AttributeSchema[];
  onEdit?: (attr: AttributeSchema) => void;
  onDelete?: (attr: AttributeSchema) => void;
}

export function AttributeSchemaEditor({ attributes, onEdit, onDelete }: Props) {
  const hasActions = Boolean(onEdit || onDelete);
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Attribute</TableHead>
          <TableHead>Display name</TableHead>
          <TableHead>Type</TableHead>
          <TableHead>Constraints</TableHead>
          <TableHead>Scope</TableHead>
          {hasActions && <TableHead className="w-[120px]" />}
        </TableRow>
      </TableHeader>
      <TableBody>
        {attributes.map((attr) => {
          const isGlobal = attr.client_id === null;
          return (
            <TableRow key={attr.id}>
              <TableCell>
                <code className="text-xs font-mono bg-muted px-1.5 py-0.5 rounded">
                  {attr.attribute_id}
                </code>
              </TableCell>
              <TableCell>
                <div className="font-medium">{attr.display_name}</div>
                {attr.description && (
                  <div className="text-xs text-muted-foreground mt-0.5">
                    {attr.description}
                  </div>
                )}
              </TableCell>
              <TableCell>
                <Badge variant="outline">{attr.data_type}</Badge>
                {attr.enum_values && (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {attr.enum_values.slice(0, 4).map((v) => (
                      <span
                        key={v}
                        className="text-[10px] bg-muted px-1.5 py-0.5 rounded font-mono"
                      >
                        {v}
                      </span>
                    ))}
                    {attr.enum_values.length > 4 && (
                      <span className="text-[10px] text-muted-foreground">
                        +{attr.enum_values.length - 4} more
                      </span>
                    )}
                  </div>
                )}
              </TableCell>
              <TableCell>
                <div className="flex flex-wrap gap-1">
                  {attr.is_required && <Badge variant="warn">Required</Badge>}
                  {attr.is_pii && (
                    <Badge variant="error" className="gap-1">
                      <Lock className="size-3" /> PII
                    </Badge>
                  )}
                  {attr.derived_from && (
                    <Badge variant="primary">Derived from {attr.derived_from}</Badge>
                  )}
                </div>
              </TableCell>
              <TableCell>
                {isGlobal ? (
                  <Badge variant="default" className="gap-1">
                    <Globe className="size-3" /> Global default
                  </Badge>
                ) : (
                  <Badge variant="primary">Client-specific</Badge>
                )}
              </TableCell>
              {hasActions && (
                <TableCell>
                  <div className="flex gap-1 justify-end">
                    {onEdit && (
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        onClick={() => onEdit(attr)}
                        aria-label="Edit attribute"
                      >
                        <Pencil className="size-3.5" />
                      </Button>
                    )}
                    {onDelete && (
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        onClick={() => onDelete(attr)}
                        aria-label="Delete attribute"
                        className="text-error hover:text-error"
                      >
                        <Trash2 className="size-3.5" />
                      </Button>
                    )}
                  </div>
                </TableCell>
              )}
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
