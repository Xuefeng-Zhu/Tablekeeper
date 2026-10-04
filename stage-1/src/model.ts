export type Credential = {
  algorithm: string;
  N: number;
  r: number;
  p: number;
  salt: string;
  hash: string;
};
export type User = {
  id: string;
  email: string;
  display_name: string;
  credential: Credential;
};
export type Booking = {
  id: string;
  reference: string;
  user_id: string;
  restaurant_id: string;
  table_ids: string[];
  party_size: number;
  status: "confirmed" | "cancelled";
  starts_at_local: string;
  start: number;
  end: number;
  created_at: string;
  rules: {
    timezone: string;
    reservation_duration_minutes: number;
    cancellation_cutoff_minutes: number;
  };
};
export type State = {
  schema_version: 1;
  users: User[];
  sessions: { token: string; user_id: string }[];
  restaurants: any[];
  reservations: Booking[];
  receipts: {
    user_id: string;
    method: string;
    path: string;
    key: string;
    body: any;
    canonical: string;
    response: any;
  }[];
};
export const empty = (): State => ({
  schema_version: 1,
  users: [],
  sessions: [],
  restaurants: [],
  reservations: [],
  receipts: [],
});
