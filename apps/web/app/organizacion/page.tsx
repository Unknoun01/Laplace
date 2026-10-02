"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { BackendDown, Cargando } from "@/components/states";
import {
  type AuditEvent,
  type Me,
  type Org,
  type Rol,
  cancelInvite,
  changePassword,
  getAudit,
  getMe,
  getOrg,
  invite,
  logoutAll,
  removeMember,
  sendVerification,
  setMemberRole,
} from "@/lib/api";
import { timestamp } from "@/lib/format";
import { Aviso, useAviso } from "./aviso";
import { Claves, Copiable } from "./claves";
import { Sso } from "./sso";
import { tr } from "@/lib/i18n";
import { t } from "@/lib/textos";

const ROLES: Rol[] = ["lector", "miembro", "admin", "propietario"];

const ES_ADMIN = (rol: Rol) => rol === "admin" || rol === "propietario";
const NOMBRE_ROL = {
  lector: "rol.lector",
  miembro: "rol.miembro",
  admin: "rol.admin",
  propietario: "rol.propietario",
} as const;
const rolDe = (rol: Rol) => t(NOMBRE_ROL[rol] ?? "rol.lector");

/**
 * La organización (D-127): quién está, con qué rol, qué claves hay y qué ha pasado.
 *
 * Lo que no puedes hacer no se enseña deshabilitado: no se enseña. Un lector ve quién
 * está y nada más; un admin ve además invitaciones, claves y auditoría.
 */
function Contenido() {
  const pedida = useSearchParams().get("org") ?? "";
  const [me, setMe] = useState<Me | null>(null);
  const [org, setOrg] = useState<Org | null>(null);
  const [error, setError] = useState("");

  const cargar = useCallback(async () => {
    const yo = await getMe();
    setMe(yo);
    if (yo.mode === "local") return;
    if (!yo.user) {
      window.location.href = `/entrar?next=${encodeURIComponent("/organizacion")}`;
      return;
    }
    const id = yo.orgs?.find((o) => o.id === pedida)?.id ?? yo.orgs?.[0]?.id;
    if (id) setOrg(await getOrg(id));
  }, [pedida]);

  useEffect(() => {
    cargar().catch((e) => setError(e instanceof Error ? e.message : t("org.error.cargar")));
  }, [cargar]);

  if (error) return <BackendDown mensaje={error} />;
  if (!me) return <Cargando />;
  if (me.mode === "local") {
    return (
      <main className="reading">
        <div className="state">
          <h2>{t("org.local.titulo")}</h2>
          <p>{t("org.local.texto")}</p>
        </div>
      </main>
    );
  }
  if (!me.user) return <Cargando />;

  return (
    <main className="reading ajustes">
      <section className="sec" style={{ paddingBottom: 0 }}>
        <h2>{org ? org.name : t("org.tu_cuenta")}</h2>
        <p className="lead">
          {org ? (
            <>
              {tr("org.eres", { rol: <strong>{rolDe(org.role)}</strong> })}
              {(me.orgs?.length ?? 0) > 1 && <CambiarOrg me={me} actual={org.id} />}
            </>
          ) : (
            t("org.sin_org")
          )}
        </p>
      </section>

      {org && <Miembros org={org} yo={me.user.id} onChange={cargar} />}
      {org && ES_ADMIN(org.role) && <Invitaciones org={org} onChange={cargar} />}
      {org && ES_ADMIN(org.role) && <Claves org={org} onChange={cargar} />}
      {org && ES_ADMIN(org.role) && <Sso org={org} me={me} />}
      {org && ES_ADMIN(org.role) && <Auditoria org={org} />}
      <TuCuenta me={me} />
    </main>
  );
}

function CambiarOrg({ me, actual }: { me: Me; actual: string }) {
  return (
    <>
      {" "}
      <select
        className="field"
        value={actual}
        onChange={(e) => (window.location.href = `/organizacion?org=${e.target.value}`)}
        aria-label={t("org.org")}
      >
        {me.orgs?.map((o) => (
          <option key={o.id} value={o.id}>
            {o.name}
          </option>
        ))}
      </select>
    </>
  );
}

function Miembros({ org, yo, onChange }: { org: Org; yo: string; onChange: () => void }) {
  const { msg, intentar } = useAviso();
  const admin = ES_ADMIN(org.role);
  return (
    <section className="sec">
      <h3>{t("org.miembros")}</h3>
      <div className="tbl-scroll">
        <table className="tabla-simple ancha">
          <thead>
            <tr>
              <th>{t("org.col.persona")}</th>
              <th>{t("org.col.rol")}</th>
              <th>{t("org.col.desde")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {org.members.map((m) => (
              <tr key={m.user_id}>
                <td>
                  {m.name || m.email}
                  {m.name && <small className="muted"> · {m.email}</small>}
                  {m.user_id === yo && <small className="muted">{t("org.tu")}</small>}
                </td>
                <td>
                  {admin ? (
                    <select
                      className="field"
                      value={m.role}
                      aria-label={t("org.rol_de", { email: m.email })}
                      onChange={async (e) => {
                        if (
                          await intentar(
                            () => setMemberRole(org.id, m.user_id, e.target.value as Rol),
                            t("org.ahora_es", { email: m.email, rol: rolDe(e.target.value as Rol) }),
                          )
                        )
                          onChange();
                      }}
                    >
                      {ROLES.map((r) => (
                        <option key={r} value={r}>
                          {rolDe(r)}
                        </option>
                      ))}
                    </select>
                  ) : (
                    rolDe(m.role)
                  )}
                </td>
                <td className="num">{timestamp(m.since)}</td>
                <td>
                  {(admin || m.user_id === yo) && (
                    <button
                      type="button"
                      className="btn small"
                      onClick={async () => {
                        const texto =
                          m.user_id === yo
                            ? t("org.salir_confirm")
                            : t("org.quitar_confirm", { email: m.email });
                        if (!window.confirm(texto)) return;
                        if (await intentar(() => removeMember(org.id, m.user_id), t("org.hecho"))) {
                          if (m.user_id === yo) window.location.href = "/";
                          else onChange();
                        }
                      }}
                    >
                      {m.user_id === yo ? t("org.salir") : t("comun.quitar")}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="disclaimer">
        {t("org.roles.nota")}
      </p>
      <Aviso {...msg} />
    </section>
  );
}

// ---------------------------------------------------------------------------------

function Invitaciones({ org, onChange }: { org: Org; onChange: () => void }) {
  const [email, setEmail] = useState("");
  const [rol, setRol] = useState<Rol>("miembro");
  const [enlace, setEnlace] = useState<{
    link: string;
    emailed: boolean;
    not_emailed_reason?: string;
  } | null>(null);
  const { msg, intentar } = useAviso();

  return (
    <section className="sec">
      <h3>{t("org.invitar")}</h3>
      <div className="ab">
        <label className="grow">
          <small>{t("org.email")}</small>
          <input
            className="field"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder={t("org.email_placeholder")}
          />
        </label>
        <label>
          <small>{t("org.col.rol")}</small>
          <select className="field" value={rol} onChange={(e) => setRol(e.target.value as Rol)}>
            <option value="lector">{rolDe("lector")}</option>
            <option value="miembro">{rolDe("miembro")}</option>
            <option value="admin">{rolDe("admin")}</option>
          </select>
        </label>
        <button
          type="button"
          className="btn primary"
          disabled={!email.includes("@")}
          onClick={async () => {
            let r: Awaited<ReturnType<typeof invite>> | null = null;
            if (await intentar(async () => (r = await invite(org.id, email.trim(), rol)), "")) {
              setEnlace(r);
              setEmail("");
              onChange();
            }
          }}
        >
          {t("org.invitar")}
        </button>
      </div>
      {enlace && (
        <div className="una-vez">
          <p>
            {enlace.emailed
              ? t("org.enviado")
              : enlace.not_emailed_reason || t("org.sin_correo")}
          </p>
          <Copiable texto={enlace.link} />
        </div>
      )}
      {(org.invitations?.length ?? 0) > 0 && (
        <>
          <p className="muted" style={{ marginTop: 16 }}>
            {t("org.pendientes")}
          </p>
          <ul className="lista-simple">
            {org.invitations!.map((i) => (
              <li key={i.email}>
                {i.email} · {rolDe(i.role)} · {t("org.caduca", { fecha: timestamp(i.expires_at) })}{" "}
                <button
                  type="button"
                  className="btn small"
                  onClick={async () => {
                    if (await intentar(() => cancelInvite(org.id, i.email), t("org.anulada")))
                      onChange();
                  }}
                >
                  {t("org.anular")}
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
      <Aviso {...msg} />
    </section>
  );
}

const ACCIONES = {
  configurar_instalacion: "org.acc.configurar_instalacion",
  login: "org.acc.login",
  login_fallido: "org.acc.login_fallido",
  invitar: "org.acc.invitar",
  anular_invitacion: "org.acc.anular_invitacion",
  aceptar_invitacion: "org.acc.aceptar_invitacion",
  cambiar_rol: "org.acc.cambiar_rol",
  quitar_miembro: "org.acc.quitar_miembro",
  crear_clave: "org.acc.crear_clave",
  revocar_clave: "org.acc.revocar_clave",
  borrar_proyecto: "org.acc.borrar_proyecto",
  login_sso: "org.acc.login_sso",
  sso_rechazado: "org.acc.sso_rechazado",
  configurar_sso: "org.acc.configurar_sso",
  quitar_sso: "org.acc.quitar_sso",
  dominios_sso: "org.acc.dominios_sso",
  crear_token_scim: "org.acc.crear_token_scim",
  revocar_token_scim: "org.acc.revocar_token_scim",
  scim_alta: "org.acc.scim_alta",
  scim_baja: "org.acc.scim_baja",
  scim_activar: "org.acc.scim_activar",
  scim_desactivar: "org.acc.scim_desactivar",
} as const;
const accion = (clave: string) =>
  clave in ACCIONES ? t(ACCIONES[clave as keyof typeof ACCIONES]) : clave;

function Auditoria({ org }: { org: Org }) {
  const [eventos, setEventos] = useState<AuditEvent[] | null>(null);
  useEffect(() => {
    getAudit(org.id)
      .then((r) => setEventos(r.events))
      .catch(() => setEventos([]));
  }, [org.id]);

  return (
    <section className="sec">
      <details>
        <summary className="sec-summary">{t("org.registro")}</summary>
        {eventos === null ? (
          <p className="muted">{t("org.cargando")}</p>
        ) : eventos.length === 0 ? (
          <p className="muted">{t("org.nada")}</p>
        ) : (
          <div className="tbl-scroll">
            <table className="tabla-simple ancha">
              <tbody>
                {eventos.map((e, i) => (
                  <tr key={`${e.at}-${i}`}>
                    <td className="num">{timestamp(e.at)}</td>
                    <td>
                      {e.email || t("org.alguien")} {accion(e.action)}{" "}
                      <span className="muted">{e.target}</span>
                    </td>
                    <td className="num muted">{e.ip}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </details>
    </section>
  );
}

// ---------------------------------------------------------------------------------

function TuCuenta({ me }: { me: Me }) {
  const [actual, setActual] = useState("");
  const [nueva, setNueva] = useState("");
  const { msg, intentar } = useAviso();

  return (
    <section className="sec">
      <h3>{t("org.tu_cuenta")}</h3>
      <p className="lead">
        {me.user?.email}
        {me.user?.is_admin && t("org.admin_instalacion")}
      </p>
      {/* Verificar el correo (D-182): sin servidor de correo no se puede, y se dice. */}
      {me.user && !me.user.email_verified && (
        <p className="muted verificar">
          {me.user.can_verify ? t("org.sin_verificar") : t("org.sin_verificar_sin_correo")}{" "}
          {me.user.can_verify && (
            <button
              type="button"
              className="btn small"
              onClick={() => intentar(() => sendVerification(), t("org.verificacion_enviada"))}
            >
              {t("org.verificar")}
            </button>
          )}
        </p>
      )}
      <div className="ab">
        <label>
          <small>{t("org.pass_actual")}</small>
          <input
            className="field"
            type="password"
            value={actual}
            onChange={(e) => setActual(e.target.value)}
            autoComplete="current-password"
          />
        </label>
        <label>
          <small>{t("org.pass_nueva")}</small>
          <input
            className="field"
            type="password"
            value={nueva}
            onChange={(e) => setNueva(e.target.value)}
            autoComplete="new-password"
          />
        </label>
        <button
          type="button"
          className="btn"
          disabled={!actual || nueva.length < 10}
          onClick={async () => {
            if (
              await intentar(
                () => changePassword(actual, nueva),
                t("org.pass_cambiada"),
              )
            ) {
              setActual("");
              setNueva("");
            }
          }}
        >
          {t("org.pass_cambiar")}
        </button>
      </div>
      <div className="actions" style={{ paddingTop: 10 }}>
        <button
          type="button"
          className="btn"
          onClick={async () => {
            if (await intentar(() => logoutAll(), "")) window.location.href = "/entrar";
          }}
        >
          {t("org.cerrar_todo")}
        </button>
      </div>
      <Aviso {...msg} />
    </section>
  );
}

export default function OrganizacionPage() {
  return (
    <Suspense fallback={<Cargando />}>
      <Contenido />
    </Suspense>
  );
}
