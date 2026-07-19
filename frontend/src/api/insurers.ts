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
  // Products are invalidated too: `in_use` is derived from Product.insurer, so
  // a product edit can change a catalog row's badge and vice versa.
  return () => {
    void qc.invalidateQueries({ queryKey: ["insurers"] });
    void qc.invalidateQueries({ queryKey: ["products"] });
  };
}

export function useInsurers() {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["insurers", cid],
    queryFn: () => api.get<Insurer[]>("/schemas/insurers"),
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
