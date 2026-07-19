import { useState } from "react";
import { Plus } from "lucide-react";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { SchemaAttributesPage } from "./attributes";
import { SchemaInsurersPage } from "./insurers";
import { SchemaProductsPage } from "./products";

const TABS = [
  { key: "attributes", label: "Employee attributes" },
  { key: "products", label: "Products catalog" },
  { key: "insurers", label: "Insurers" },
] as const;

type SchemaTab = (typeof TABS)[number]["key"];

// Attributes + products are twin CRUD surfaces over the client schema; they
// live as tabs of one page so the sidebar stays flat. The active tab rides
// the ?tab= search param so both views stay deep-linkable. The "Add" action
// sits on the tab row; its sheet's open state is lifted here (edit/draft stay
// local to each tab — the sheet always resets on close, so a bare open flag
// is enough).
export function SchemaPage() {
  const navigate = useNavigate();
  const search = useSearch({ strict: false }) as { tab?: string };
  const tab: SchemaTab =
    search.tab === "products" || search.tab === "insurers"
      ? search.tab
      : "attributes";
  const [addAttrOpen, setAddAttrOpen] = useState(false);
  const [addProductOpen, setAddProductOpen] = useState(false);
  const [addInsurerOpen, setAddInsurerOpen] = useState(false);

  return (
    <Tabs
      value={tab}
      onValueChange={(value) =>
        navigate({ to: "/schema", search: { tab: value } })
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
        {tab === "attributes" && (
          <Button onClick={() => setAddAttrOpen(true)}>
            <Plus className="size-4" /> Add attribute
          </Button>
        )}
        {tab === "products" && (
          <Button onClick={() => setAddProductOpen(true)}>
            <Plus className="size-4" /> Add product
          </Button>
        )}
        {tab === "insurers" && (
          <Button onClick={() => setAddInsurerOpen(true)}>
            <Plus className="size-4" /> Add insurer
          </Button>
        )}
      </div>
      <TabsContent value="attributes">
        <SchemaAttributesPage open={addAttrOpen} onOpenChange={setAddAttrOpen} />
      </TabsContent>
      <TabsContent value="products">
        <SchemaProductsPage open={addProductOpen} onOpenChange={setAddProductOpen} />
      </TabsContent>
      <TabsContent value="insurers">
        <SchemaInsurersPage open={addInsurerOpen} onOpenChange={setAddInsurerOpen} />
      </TabsContent>
    </Tabs>
  );
}
