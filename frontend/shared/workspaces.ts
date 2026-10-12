import type { Workspace } from "./types";
import type { WorkspaceMember } from "./governance";
export type SpaceSummary=Workspace & {status:"active"|"archived";revision:number;archived_at:string|null;administrators:{user_id:string;username:string|null;display_name:string}[];member_count:number;can_manage:boolean;can_open:boolean;can_archive:boolean;can_rename:boolean};
export type MemberDisplay=WorkspaceMember & {username:string|null;display_name:string;account_active:boolean};
export type Paged<T>={items:T[];total:number;limit:number;offset:number};
export type EffectiveAccess={member:MemberDisplay;workspace:{id:string;name:string;status:string};roles:string[];permissions:{key:string;label:string;allowed:boolean}[];agents:{id:string;name:string;allowed:boolean;reason:string}[];functions:{id:string;name:string;allowed:boolean;reason:string}[]};
