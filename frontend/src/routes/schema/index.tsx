import { useNavigate, useSearch } from "@tanstack/react-router";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { SchemaAttributesPage } from "./attributes";
import { SchemaProductsPage } from "./products";

const TABS = [
  { key: "attributes", label: "Employee attributes" },
  { key: "products", label: "Products catalog" },
] as const;

type SchemaTab = (typeof TABS)[number]["key"];

// Attributes + products are twin CRUD surfaces over the client schema; they
// live as tabs of one page so the sidebar stays flat. The active tab rides
// the ?tab= search param so both views stay deep-linkable.
export function SchemaPage() {
  const navigate = useNavigate();
  const search = useSearch({ strict: false }) as { tab?: string };
  const tab: SchemaTab = search.tab === "products" ? "products" : "attributes";

  return (
    <Tabs
      value={tab}
      onValueChange={(value) =>
        navigate({ to: "/schema", search: { tab: value } })
      }
    >
      <TabsList>
        {TABS.map((t) => (
          <TabsTrigger key={t.key} value={t.key}>
            {t.label}
          </TabsTrigger>
        ))}
      </TabsList>
      <TabsContent value="attributes">
        <SchemaAttributesPage />
      </TabsContent>
      <TabsContent value="products">
        <SchemaProductsPage />
      </TabsContent>
    </Tabs>
  );
}
