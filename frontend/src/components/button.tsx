"use client";
import { Slot } from "@radix-ui/react-slot";
import type { ButtonHTMLAttributes } from "react";
import styles from "./button.module.css";
type Props = ButtonHTMLAttributes<HTMLButtonElement> & { asChild?: boolean };
export function Button({ asChild = false, className = "", ...props }: Props) { const Component = asChild ? Slot : "button"; return <Component className={`${styles.button} ${className}`} {...props} />; }
