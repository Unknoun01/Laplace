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
  createKey,
  getAudit,
  getMe,
  getOrg,
  invite,
  logoutAll,
  removeMember,
  revokeKey,
  setMemberRole,
} from "@/lib/api";
import { timestamp } from "@/lib/format";

const ROLES: Rol[] = ["lector", "miembro", "admin", "propietario"];
const ES_ADMIN = (rol: Rol) => rol === "admin" || rol === "propietario";

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
    cargar().catch((e) => setError(e instanceof Error ? e.message : "no se ha podido cargar"));
  }, [cargar]);

  if (error) return <BackendDown mensaje={error} />;
  if (!me) return <Cargando />;
  if (me.mode === "local") {
    return (
      <main className="reading">
        <div className="state">
          <h2>El modo local no tiene cuentas</h2>
          <p>
            Es un proceso en tu máquina con tus trazas: no hay nadie más a quien darle
            acceso. Las cuentas, los roles y las claves existen en la instalación de nube.
          </p>
        </div>
      </main>
    );
  }
  if (!me.user) return <Cargando />;

  return (
    <main className="reading ajustes">
      <section className="sec" style={{ paddingBottom: 0 }}>
        <h2>{org ? org.name : "Tu cuenta"}</h2>
        <p className="lead">
          {org ? (
            <>
              Eres <strong>{org.role}</strong> de esta organización.
              {(me.orgs?.length ?? 0) > 1 && <CambiarOrg me={me} actual={org.id} />}
            </>
          ) : (
            "No perteneces a ninguna organización todavía: pide una invitación."
          )}
        </p>
      </section>

      {org && <Miembros org={org} yo={me.user.id} onChange={cargar} />}
      {org && ES_ADMIN(org.role) && <Invitaciones org={org} onChange={cargar} />}
      {org && ES_ADMIN(org.role) && <Claves org={org} onChange={cargar} />}
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
        aria-label="Organización"
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

function Aviso({ ok, texto }: { ok: boolean; texto: string }) {
  if (!texto) return null;
  return <p className={ok ? "vok" : "verr"}>{texto}</p>;
}

function useAviso() {
  const [msg, setMsg] = useState({ ok: true, texto: "" });
  const intentar = async (fn: () => Promise<unknown>, texto: string) => {
    try {
      await fn();
      setMsg({ ok: true, texto });
      return true;
    } catch (e) {
      setMsg({ ok: false, texto: e instanceof Error ? e.message : "no se ha podido" });
      return false;
    }
  };
  return { msg, intentar };
}

// ---------------------------------------------------------------------------------

function Miembros({ org, yo, onChange }: { org: Org; yo: string; onChange: () => void }) {
  const { msg, intentar } = useAviso();
  const admin = ES_ADMIN(org.role);
  return (
    <section className="sec">
      <h3>Miembros</h3>
      <table className="tabla-simple ancha">
        <thead>
          <tr>
            <th>Persona</th>
            <th>Rol</th>
            <th>Desde</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {org.members.map((m) => (
            <tr key={m.user_id}>
              <td>
                {m.name || m.email}
                {m.name && <small className="muted"> · {m.email}</small>}
                {m.user_id === yo && <small className="muted"> · tú</small>}
              </td>
              <td>
                {admin ? (
                  <select
                    className="field"
                    value={m.role}
                    aria-label={`Rol de ${m.email}`}
                    onChange={async (e) => {
                      if (
                        await intentar(
                          () => setMemberRole(org.id, m.user_id, e.target.value as Rol),
                          `${m.email} ahora es ${e.target.value}.`,
                        )
                      )
                        onChange();
                    }}
                  >
                    {ROLES.map((r) => (
                      <option key={r} value={r}>
                        {r}
                      </option>
                    ))}
                  </select>
                ) : (
                  m.role
                )}
              </td>
              <td className="num">{timestamp(m.since)}</td>
              <td>
                {(admin || m.user_id === yo) && (
                  <button
                    type="button"
                    className="btn small"
                    onClick={async () => {
                      const texto = m.user_id === yo ? "¿Salir de la organización?" : `¿Quitar a ${m.email}?`;
                      if (!window.confirm(texto)) return;
                      if (await intentar(() => removeMember(org.id, m.user_id), "Hecho.")) {
                        if (m.user_id === yo) window.location.href = "/";
                        else onChange();
                      }
                    }}
                  >
                    {m.user_id === yo ? "Salir" : "Quitar"}
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="disclaimer">
        Lector: ve. Miembro: además anota ejecuciones, marca problemas y gestiona prompts y
        conjuntos. Admin: además miembros, claves, alertas, presupuesto y borrar. Quitar a
        alguien cierra sus sesiones al momento.
      </p>
      <Aviso {...msg} />
    </section>
  );
}

// ---------------------------------------------------------------------------------

function Invitaciones({ org, onChange }: { org: Org; onChange: () => void }) {
  const [email, setEmail] = useState("");
  const [rol, setRol] = useState<Rol>("miembro");
  const [enlace, setEnlace] = useState<{ link: string; emailed: boolean } | null>(null);
  const { msg, intentar } = useAviso();

  return (
    <section className="sec">
      <h3>Invitar</h3>
      <div className="ab">
        <label className="grow">
          <small>Email</small>
          <input
            className="field"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="persona@empresa.com"
          />
        </label>
        <label>
          <small>Rol</small>
          <select className="field" value={rol} onChange={(e) => setRol(e.target.value as Rol)}>
            <option value="lector">lector</option>
            <option value="miembro">miembro</option>
            <option value="admin">admin</option>
          </select>
        </label>
        <button
          type="button"
          className="btn primary"
          disabled={!email.includes("@")}
          onClick={async () => {
            let r: { link: string; emailed: boolean } | null = null;
            if (await intentar(async () => (r = await invite(org.id, email.trim(), rol)), "")) {
              setEnlace(r);
              setEmail("");
              onChange();
            }
          }}
        >
          Invitar
        </button>
      </div>
      {enlace && (
        <div className="una-vez">
          <p>
            {enlace.emailed
              ? "Le hemos mandado el enlace por correo. También puedes copiarlo:"
              : "Esta instalación no tiene correo configurado: mándale tú este enlace. Sólo se enseña ahora, y caduca en 7 días."}
          </p>
          <Copiable texto={enlace.link} />
        </div>
      )}
      {(org.invitations?.length ?? 0) > 0 && (
        <>
          <p className="muted" style={{ marginTop: 16 }}>
            Pendientes
          </p>
          <ul className="lista-simple">
            {org.invitations!.map((i) => (
              <li key={i.email}>
                {i.email} · {i.role} · caduca el {timestamp(i.expires_at)}{" "}
                <button
                  type="button"
                  className="btn small"
                  onClick={async () => {
                    if (await intentar(() => cancelInvite(org.id, i.email), "Invitación anulada."))
                      onChange();
                  }}
                >
                  Anular
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

function Copiable({ texto }: { texto: string }) {
  const [copiado, setCopiado] = useState(false);
  return (
    <div className="copiable">
      <code>{texto}</code>
      <button
        type="button"
        className="btn small"
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(texto);
            setCopiado(true);
          } catch {
            /* sin permiso de portapapeles: queda seleccionable a mano */
          }
        }}
      >
        {copiado ? "Copiado" : "Copiar"}
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------------

function Claves({ org, onChange }: { org: Org; onChange: () => void }) {
  const [proyecto, setProyecto] = useState(org.projects[0] ?? "");
  const [nombre, setNombre] = useState("");
  const [dias, setDias] = useState(0);
  const [nueva, setNueva] = useState<{ key: string; project_id: string } | null>(null);
  const { msg, intentar } = useAviso();
  const activas = (org.keys ?? []).filter((k) => !k.revoked_at);
  const ahora = new Date().toISOString();

  return (
    <section className="sec">
      <h3>Claves de API</h3>
      <p className="lead">
        Una clave por proyecto: es con lo que tu agente manda trazas. Escribe un nombre de
        proyecto nuevo para crearlo.
      </p>
      <div className="ab">
        <label className="grow">
          <small>Proyecto</small>
          <input
            className="field"
            list="proyectos-org"
            value={proyecto}
            onChange={(e) => setProyecto(e.target.value)}
            placeholder="mi-agente"
          />
          <datalist id="proyectos-org">
            {org.projects.map((p) => (
              <option key={p} value={p} />
            ))}
          </datalist>
        </label>
        <label className="grow">
          <small>Para qué es</small>
          <input
            className="field"
            value={nombre}
            onChange={(e) => setNombre(e.target.value)}
            placeholder="ingesta producción"
          />
        </label>
        <label>
          <small>Caduca</small>
          <select className="field" value={dias} onChange={(e) => setDias(Number(e.target.value))}>
            <option value={0}>nunca</option>
            <option value={30}>en 30 días</option>
            <option value={90}>en 90 días</option>
            <option value={365}>en un año</option>
          </select>
        </label>
        <button
          type="button"
          className="btn primary"
          disabled={!/^[A-Za-z0-9._-]+$/.test(proyecto)}
          onClick={async () => {
            let r: { key: string; project_id: string } | null = null;
            const ok = await intentar(async () => {
              r = await createKey({
                org_id: org.id,
                project_id: proyecto,
                name: nombre,
                expires_days: dias,
              });
            }, "");
            if (ok) {
              setNueva(r);
              setNombre("");
              onChange();
            }
          }}
        >
          Crear clave
        </button>
      </div>
      {nueva && (
        <div className="una-vez">
          <p>
            <strong>Cópiala ahora:</strong> sólo se guarda su huella y no se puede volver a
            ver. Si la pierdes, revócala y crea otra.
          </p>
          <Copiable texto={nueva.key} />
          <pre>{`import laplace
laplace.init(
    project="${nueva.project_id}",
    endpoint="${typeof window === "undefined" ? "" : window.location.origin}",
    api_key="${nueva.key}",
)`}</pre>
        </div>
      )}
      {activas.length > 0 && (
        <table className="tabla-simple ancha">
          <thead>
            <tr>
              <th>Proyecto</th>
              <th>Nombre</th>
              <th>Último uso</th>
              <th>Caduca</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {activas.map((k) => (
              <tr key={k.id}>
                <td className="num">{k.project_id}</td>
                <td>
                  {k.name}
                  {k.created_by && <small className="muted"> · {k.created_by}</small>}
                </td>
                <td className="num">{k.last_used_at ? timestamp(k.last_used_at) : "nunca"}</td>
                <td className={`num${k.expires_at && k.expires_at < ahora ? " verr" : ""}`}>
                  {k.expires_at ? timestamp(k.expires_at) : "no caduca"}
                </td>
                <td>
                  <button
                    type="button"
                    className="btn small danger"
                    onClick={async () => {
                      if (!window.confirm(`¿Revocar «${k.name}»? Deja de servir al momento.`))
                        return;
                      if (await intentar(() => revokeKey(org.id, k.id), "Clave revocada."))
                        onChange();
                    }}
                  >
                    Revocar
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="disclaimer">
        Para rotar una clave: crea la nueva, cámbiala en tu agente y revoca la vieja.
      </p>
      <Aviso {...msg} />
    </section>
  );
}

// ---------------------------------------------------------------------------------

const ACCIONES: Record<string, string> = {
  configurar_instalacion: "configuró la instalación",
  login: "entró",
  login_fallido: "intento de entrada fallido",
  invitar: "invitó a",
  anular_invitacion: "anuló la invitación de",
  aceptar_invitacion: "aceptó la invitación como",
  cambiar_rol: "cambió el rol de",
  quitar_miembro: "quitó a",
  crear_clave: "creó una clave para",
  revocar_clave: "revocó la clave",
  borrar_proyecto: "borró el proyecto",
};

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
        <summary className="sec-summary">Registro de actividad</summary>
        {eventos === null ? (
          <p className="muted">Cargando…</p>
        ) : eventos.length === 0 ? (
          <p className="muted">Nada todavía.</p>
        ) : (
          <table className="tabla-simple ancha">
            <tbody>
              {eventos.map((e, i) => (
                <tr key={`${e.at}-${i}`}>
                  <td className="num">{timestamp(e.at)}</td>
                  <td>
                    {e.email || "alguien"} {ACCIONES[e.action] ?? e.action}{" "}
                    <span className="muted">{e.target}</span>
                  </td>
                  <td className="num muted">{e.ip}</td>
                </tr>
              ))}
            </tbody>
          </table>
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
      <h3>Tu cuenta</h3>
      <p className="lead">
        {me.user?.email}
        {me.user?.is_admin && " · administras esta instalación"}
      </p>
      <div className="ab">
        <label>
          <small>Contraseña actual</small>
          <input
            className="field"
            type="password"
            value={actual}
            onChange={(e) => setActual(e.target.value)}
            autoComplete="current-password"
          />
        </label>
        <label>
          <small>Nueva (10 caracteres o más)</small>
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
                "Contraseña cambiada. Las demás sesiones se han cerrado.",
              )
            ) {
              setActual("");
              setNueva("");
            }
          }}
        >
          Cambiar contraseña
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
          Cerrar sesión en todos los dispositivos
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
