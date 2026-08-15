import styles from "./states.module.css";
export function LoadingState({ label = "Loading" }: { label?: string }) { return <div role="status" aria-live="polite" className={styles.card}>{label}…</div>; }
export function ErrorState({ title = "Something went wrong", detail }: { title?: string; detail?: string }) { return <section role="alert" className={styles.card}><h2>{title}</h2>{detail && <p>{detail}</p>}</section>; }
export function EmptyState({ title, description }: { title:string; description:string }) { return <section className={styles.card}><h1>{title}</h1><p>{description}</p></section>; }
