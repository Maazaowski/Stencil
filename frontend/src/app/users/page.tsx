"use client";

import { useState } from "react";
import { toast } from "sonner";
import { UserPlus, Users as UsersIcon, Trash2 } from "lucide-react";
import { PageHeader } from "@/components/page-header";
import { ErrorState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useSession } from "@/hooks/use-session";
import { useConfirm } from "@/components/ui/confirm-dialog";
import {
  useCreateUser,
  useDeleteUser,
  useUpdateUser,
  useUsers,
  type AppUser,
  type UpdateUserInput,
} from "@/hooks/use-users";

function lastLogin(value: string | null) {
  if (!value) return "Never";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Never" : date.toLocaleString();
}

export default function UsersPage() {
  const { data: session } = useSession();
  const isAdmin = !!session?.is_admin;
  const { data: users, isLoading, isError, refetch } = useUsers(isAdmin);
  const createUser = useCreateUser();
  const updateUser = useUpdateUser();
  const deleteUser = useDeleteUser();
  const confirm = useConfirm();
  const [newUser, setNewUser] = useState({
    email: "",
    username: "",
    password: "",
    role: "user" as "admin" | "user",
  });
  const [passwords, setPasswords] = useState<Record<number, string>>({});

  async function handleCreate() {
    try {
      await createUser.mutateAsync({
        email: newUser.email,
        username: newUser.username || null,
        password: newUser.password,
        role: newUser.role,
      });
      setNewUser({ email: "", username: "", password: "", role: "user" });
      toast.success("User created.");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to create user.");
    }
  }

  async function patchUser(user: AppUser, body: UpdateUserInput) {
    try {
      await updateUser.mutateAsync({ id: user.id, body });
      toast.success("User updated.");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to update user.");
    }
  }

  async function resetPassword(user: AppUser) {
    const password = passwords[user.id]?.trim();
    if (!password) return;
    await patchUser(user, { password });
    setPasswords((prev) => ({ ...prev, [user.id]: "" }));
  }

  async function deactivate(user: AppUser) {
    const ok = await confirm({
      title: `Deactivate ${user.email}?`,
      description: "They will no longer be able to sign in.",
      confirmLabel: "Deactivate",
      variant: "destructive",
    });
    if (!ok) return;
    try {
      await deleteUser.mutateAsync(user.id);
      toast.success("User deactivated.");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to deactivate user.");
    }
  }

  if (session && !isAdmin) {
    return (
      <div className="space-y-4">
        <PageHeader title="Users" description="Account management" />
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            You need an admin account to manage users. To change your own password, go
            to My Account.
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="Users"
        description="Who can sign in, and what they're allowed to do. Admins manage users; everyone changes their own password under My Account."
      />

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <UserPlus className="h-4 w-4" />
            Add user
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <div className="space-y-1.5">
              <label className="text-sm font-medium" htmlFor="new-user-email">
                Email
              </label>
              <Input
                id="new-user-email"
                value={newUser.email}
                onChange={(e) => setNewUser((prev) => ({ ...prev, email: e.target.value }))}
                type="email"
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium" htmlFor="new-user-name">
                Display name
              </label>
              <Input
                id="new-user-name"
                value={newUser.username}
                onChange={(e) => setNewUser((prev) => ({ ...prev, username: e.target.value }))}
              />
              <p className="text-xs text-muted-foreground">Optional.</p>
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium" htmlFor="new-user-password">
                Temporary password
              </label>
              <Input
                id="new-user-password"
                value={newUser.password}
                onChange={(e) => setNewUser((prev) => ({ ...prev, password: e.target.value }))}
                type="password"
              />
              <p className="text-xs text-muted-foreground">
                At least 8 characters. The user can change it after signing in.
              </p>
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium">Role</label>
              <Select
                value={newUser.role}
                onValueChange={(role) => {
                  if (role === "user" || role === "admin") {
                    setNewUser((prev) => ({ ...prev, role }));
                  }
                }}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="user">User</SelectItem>
                  <SelectItem value="admin">Admin</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <Button
            className="mt-4"
            disabled={!newUser.email.trim() || newUser.password.length < 8 || createUser.isPending}
            onClick={handleCreate}
          >
            {createUser.isPending ? "Adding…" : "Add user"}
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <UsersIcon className="h-4 w-4" />
            Accounts
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto rounded-md border">
            <div className="min-w-[960px] divide-y">
              <div className="grid grid-cols-[1.4fr_1.1fr_130px_100px_1fr_280px] gap-3 bg-muted/40 px-4 py-2.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                <span>Email</span>
                <span>Name</span>
                <span>Role</span>
                <span>Status</span>
                <span>Last login</span>
                <span>Reset password</span>
              </div>
              {isLoading ? (
                <div className="px-4 py-6 text-sm text-muted-foreground" aria-busy="true">
                  Loading users…
                </div>
              ) : isError ? (
                <div className="p-3">
                  <ErrorState what="users" onRetry={() => refetch()} />
                </div>
              ) : (
                (users ?? []).map((user) => (
                  <div
                    key={user.id}
                    className="grid grid-cols-[1.4fr_1.1fr_130px_100px_1fr_280px] items-center gap-3 px-4 py-3 text-sm"
                  >
                    <span className="truncate font-medium">{user.email}</span>
                    <Input
                      defaultValue={user.username ?? ""}
                      aria-label={`Display name for ${user.email}`}
                      className="h-8"
                      onBlur={(e) => {
                        const username = e.target.value.trim();
                        if (username !== (user.username ?? "")) {
                          patchUser(user, { username });
                        }
                      }}
                    />
                    <Select
                      value={user.role}
                      onValueChange={(role) => {
                        if (role === "user" || role === "admin") {
                          patchUser(user, { role });
                        }
                      }}
                    >
                      <SelectTrigger className="h-8">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="user">User</SelectItem>
                        <SelectItem value="admin">Admin</SelectItem>
                      </SelectContent>
                    </Select>
                    <Badge variant={user.is_active ? "outline" : "secondary"}>
                      {user.is_active ? "Active" : "Inactive"}
                    </Badge>
                    <span className="truncate text-xs text-muted-foreground">
                      {lastLogin(user.last_login_at)}
                    </span>
                    <div className="flex items-center gap-2">
                      <Input
                        className="h-8"
                        type="password"
                        aria-label={`New password for ${user.email}`}
                        value={passwords[user.id] ?? ""}
                        onChange={(e) =>
                          setPasswords((prev) => ({ ...prev, [user.id]: e.target.value }))
                        }
                      />
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={(passwords[user.id] ?? "").length < 8}
                        onClick={() => resetPassword(user)}
                      >
                        Set
                      </Button>
                      {user.is_active ? (
                        <Button
                          size="sm"
                          variant="ghost"
                          className="text-destructive"
                          onClick={() => deactivate(user)}
                          title="Deactivate"
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      ) : (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => patchUser(user, { is_active: true })}
                        >
                          Restore
                        </Button>
                      )}
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
          <p className="mt-2 text-xs text-muted-foreground">
            Minimum 8 characters for passwords. The last active admin cannot be demoted or
            deactivated.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
