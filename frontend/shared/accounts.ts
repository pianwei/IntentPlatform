import type { Workspace } from "./types";
import type { TextSize } from "./displayPreferences";
export type Profile = { user_id: string; username: string; display_name: string; scope: string; workspaces: Workspace[]; fixture_mode: boolean; is_platform_admin: boolean; must_change_password: boolean; auth_method: string; preferences?: { density?: "comfortable" | "compact"; text_size?: TextSize } };
export type Account = { user_id: string; username: string; display_name: string; active: boolean; revision: number; created_at?: string; must_change_password?: boolean };
export type UserKey = { id: string; name: string; prefix?: string; created_at: string; expires_at: string | null; revoked: boolean; last_used_at?: string | null };
export type AccountOperation = { id: string; action: string; actor_id: string; target_user_id?: string; created_at: string; detail?: Record<string, unknown> };
export const roleNames: Record<string, string> = { admin: "空间管理员", maintainer: "功能维护者", publisher: "发布者", user: "业务用户" };
