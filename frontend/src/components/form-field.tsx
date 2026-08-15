"use client";
import type { InputHTMLAttributes } from "react";
import styles from "./form-field.module.css";

type Props = InputHTMLAttributes<HTMLInputElement> & { label:string; error?:string };
export function FormField({ label,error,id,...props }:Props) {
  const inputId=id ?? props.name;
  if(!inputId) throw new Error("FormField requires id or name");
  const errorId=`${inputId}-error`;
  return <div className={styles.field}><label htmlFor={inputId}>{label}</label><input id={inputId} aria-invalid={Boolean(error)} aria-describedby={error ? errorId : undefined} {...props}/>{error && <span id={errorId} role="alert">{error}</span>}</div>;
}
