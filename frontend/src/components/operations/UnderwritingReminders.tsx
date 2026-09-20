import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { api } from "@/api/client";
import { useMe } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useSession } from "@/stores/session";

type ReminderItem = {
  id: string;
  recipient_email: string;
  status: string;
  created_at: string;
  sent_at: string | null;
  follow_up_on: string | null;
  attempts: number;
  last_error: string | null;
};

type ReminderHistory = {
  delivery_enabled: boolean;
  suggested_email: string | null;
  sent_count: number;
  last_sent_at: string | null;
  next_follow_up: string | null;
  items: ReminderItem[];
};

function formatTimestamp(value: string): string {
  return new Date(value).toLocaleString();
}

function ReminderSummary({ history }: { history: ReminderHistory }) {
  return (
    <p className="text-sm text-muted-foreground" aria-live="polite">
      {history.sent_count} delivered
      {history.last_sent_at
        ? ` · Last sent ${formatTimestamp(history.last_sent_at)}`
        : ""}
      {history.next_follow_up
        ? ` · Next follow-up ${history.next_follow_up}`
        : ""}
    </p>
  );
}

function ReminderComposer({
  recipient,
  followUp,
  pending,
  onRecipientChange,
  onFollowUpChange,
  onSubmit,
}: {
  recipient: string;
  followUp: string;
  pending: boolean;
  onRecipientChange: (value: string) => void;
  onFollowUpChange: (value: string) => void;
  onSubmit: () => void;
}) {
  return (
    <form
      className="flex flex-wrap items-end gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
    >
      <label className="space-y-1 text-sm">
        Recipient email
        <Input
          type="email"
          required
          value={recipient}
          onChange={(event) => onRecipientChange(event.target.value)}
          disabled={pending}
        />
      </label>
      <label className="space-y-1 text-sm">
        Next follow-up
        <Input
          type="date"
          value={followUp}
          onChange={(event) => onFollowUpChange(event.target.value)}
          disabled={pending}
        />
      </label>
      <Button type="submit" loading={pending}>
        Queue reminder
      </Button>
      <p className="w-full text-xs text-muted-foreground">
        Sends a generic follow-up with the review reference. Medical requirements
        stay in this workspace. A review closed before delivery cancels its queued
        reminder.
      </p>
    </form>
  );
}

function ReminderHistoryTable({ items }: { items: ReminderItem[] }) {
  if (items.length === 0) return null;
  return (
    <div
      className="overflow-x-auto"
      role="region"
      aria-label="Reminder delivery history"
      tabIndex={0}
    >
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-border">
            <th className="py-2">Recipient</th>
            <th>Status</th>
            <th>Sent / queued</th>
            <th>Follow-up</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id} className="border-b border-border">
              <td className="py-2">{item.recipient_email}</td>
              <td>
                {item.status}
                {item.last_error && (
                  <span className="block text-xs text-error">{item.last_error}</span>
                )}
              </td>
              <td>{formatTimestamp(item.sent_at ?? item.created_at)}</td>
              <td>{item.follow_up_on ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function UnderwritingReminders({
  reviewId,
  open,
}: {
  reviewId: string;
  open: boolean;
}) {
  const clientId = useSession((state) => state.activeClientId);
  const { data: me } = useMe();
  const editable = me?.role === "broker_admin" || me?.role === "system_admin";
  const headers = { "X-Inspro-Client": clientId ?? "" };
  const path = `/underwriting/reviews/${reviewId}/reminders`;
  const queryKey = ["underwriting-reminders", clientId, reviewId];
  const queryClient = useQueryClient();
  const history = useQuery({
    queryKey,
    queryFn: () => api.get<ReminderHistory>(path, { headers }),
    enabled: Boolean(clientId),
    refetchInterval: 15_000,
  });
  const [recipient, setRecipient] = useState<string | null>(null);
  const [followUp, setFollowUp] = useState("");
  const [requestId, setRequestId] = useState(() => crypto.randomUUID());
  const mutation = useMutation({
    mutationFn: () =>
      api.post<ReminderHistory>(
        path,
        {
          id: requestId,
          recipient_email: recipient ?? history.data?.suggested_email ?? "",
          follow_up_on: followUp || null,
        },
        { headers },
      ),
    onSuccess: (result) => {
      queryClient.setQueryData(queryKey, result);
      setRequestId(crypto.randomUUID());
      toast.success("Reminder queued. Delivery is tracked below.");
    },
  });

  return (
    <section
      className="mt-6 space-y-3 rounded-lg border border-border p-4"
      aria-label="Underwriting reminders"
    >
      <h3 className="text-sm font-semibold">Reminders and follow-up</h3>
      {history.isError ? (
        <p role="alert">Reminder history could not be loaded.</p>
      ) : !history.data ? (
        <p role="status">Loading reminders…</p>
      ) : (
        <>
          <ReminderSummary history={history.data} />
          {!history.data.delivery_enabled && (
            <p className="text-sm text-muted-foreground">
              Outbound reminder delivery is not enabled in this environment.
            </p>
          )}
          {history.data.delivery_enabled && editable && open && (
            <ReminderComposer
              recipient={recipient ?? history.data.suggested_email ?? ""}
              followUp={followUp}
              pending={mutation.isPending}
              onRecipientChange={setRecipient}
              onFollowUpChange={setFollowUp}
              onSubmit={() => mutation.mutate()}
            />
          )}
          {mutation.isError && (
            <p role="alert" className="text-sm text-error">
              The reminder could not be queued. Review the details and try again.
            </p>
          )}
          <ReminderHistoryTable items={history.data.items} />
        </>
      )}
    </section>
  );
}
