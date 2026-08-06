import { useState } from "react";
import { Plus } from "lucide-react";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { SchemaAttributesPage } from "./attributes";
import { SchemaInsurersPage } from "./insurers";
import { SchemaProductsPage } from "./products";

// Each tab is a CRUD surface with the same shape: a panel that takes the "Add"
// sheet's open state, and a label for the button. Declared once here so adding
// a tab is one entry rather than a new branch in three places.
const TABS = [
  {
    key: "attributes",
    label: "Employee attributes",
    addLabel: "Add attribute",
    Panel: SchemaAttributesPage,
  },
  {
    key: "products",
    label: "Products catalog",
    addLabel: "Add product",
    Panel: SchemaProductsPage,
  },
  { key: "insurers", label: "Insurers", addLabel: "Add insurer", Panel: SchemaInsurersPage },
] as const;

type SchemaTab = (typeof TABS)[number]["key"];

const isTab = (v: string | undefined): v is SchemaTab =>
  TABS.some((t) => t.key === v);

// Twin CRUD surfaces over the client schema, living as tabs of one page so the
// sidebar stays flat. The active tab rides the ?tab= search param so every view
// stays deep-linkable. The "Add" action sits on the tab row; its sheet's open
// state is lifted here (edit/draft stay local to each panel — the sheet always
// resets on close, so a bare open flag per tab is enough).
export function SchemaPage() {
  const navigate = useNavigate();
  const search = useSearch({ strict: false }) as { tab?: string };
  const tab: SchemaTab = isTab(search.tab) ? search.tab : "attributes";
  const [addOpen, setAddOpen] = useState<Partial<Record<SchemaTab, boolean>>>({});
  const setOpen = (key: SchemaTab) => (open: boolean) =>
    setAddOpen((s) => ({ ...s, [key]: open }));
  const active = TABS.find((t) => t.key === tab)!;

  return (
    <Tabs
      value={tab}
      onValueChange={(value) =>
        navigate({ to: "/firm/schema", search: { tab: value } })
      }
    >
      <div className="flex items-center justify-between gap-3">
        <TabsList>
          {TABS.map((t) => (
            <TabsTrigger key={t.key} value={t.key}>
              {t.label}
            </TabsTrigger>
          ))}
        </TabsList>
        <Button onClick={() => setOpen(active.key)(true)}>
          <Plus className="size-4" /> {active.addLabel}
        </Button>
      </div>
      {TABS.map(({ key, Panel }) => (
        <TabsContent key={key} value={key}>
          <Panel open={Boolean(addOpen[key])} onOpenChange={setOpen(key)} />
        </TabsContent>
      ))}
    </Tabs>
  );
}
