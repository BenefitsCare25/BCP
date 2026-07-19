/** Typed calls + query hooks for the insurer name catalog.
 *
 * The catalog supplies the vocabulary for every insurer field; it is not a
 * foreign key, so a product still stores the insurer's `name` as a plain
 * string. Deleting or renaming an entry therefore never orphans a product —
 * it only changes what the dropdown offers.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useSession } from "@/stores/session";

export interface Insurer {
  id: string;
  client_id: string | null;
  name: string;
  legal_name: string | null;
  aliases: string[];
  notes: string | null;
  /** The name is currently stored on at least one visible product. */
  in_use: boolean;
}

export interface InsurerInput {
  name: string;
  legal_name?: string | null;
  aliases?: string[] | null;
  notes?: string | null;
}

function useInsurerInvalidator() {
  const qc = useQueryClient();
  // Products are invalidated too, since their form offers these names. The key
  // must match `useProducts` exactly at the prefix — that query is keyed
  // ["schemas", "products", clientId], so ["products"] would match nothing.
  return () => {
    void qc.invalidateQueries({ queryKey: ["insurers"] });
    void qc.invalidateQueries({ queryKey: ["schemas", "products"] });
  };
}

export function useInsurers() {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["insurers", cid],
    queryFn: () => api.get<Insurer[]>("/schemas/insurers"),
    // `in_use` is derived server-side from Product.insurer, PanelListing.insurer
    // and PanelCard.insurer, so it can be invalidated by mutations all over the
    // app. Rather than teach every one of those about this query (and miss the
    // next one), opt out of the global 30s staleTime so mounting the Insurers
    // tab — or any insurer dropdown — always reflects current usage. The
    // payload is ~20 rows.
    staleTime: 0,
  });
}

export function useCreateInsurer() {
  const invalidate = useInsurerInvalidator();
  return useMutation({
    mutationFn: (input: InsurerInput) =>
      api.post<Insurer>("/schemas/insurers", input),
    onSuccess: invalidate,
  });
}

export function useUpdateInsurer() {
  const invalidate = useInsurerInvalidator();
  return useMutation({
    mutationFn: (input: { id: string } & Partial<InsurerInput>) => {
      const { id, ...body } = input;
      return api.patch<Insurer>(`/schemas/insurers/${id}`, body);
    },
    onSuccess: invalidate,
  });
}

export function useDeleteInsurer() {
  const invalidate = useInsurerInvalidator();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`/schemas/insurers/${id}`),
    onSuccess: invalidate,
  });
}
