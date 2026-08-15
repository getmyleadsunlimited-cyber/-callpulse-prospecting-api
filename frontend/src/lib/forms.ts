"use client";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm, type UseFormProps } from "react-hook-form";
import { z } from "zod";

export function useZodForm<TSchema extends z.ZodTypeAny>(schema:TSchema, options?:UseFormProps<z.infer<TSchema>>) {
  return useForm<z.infer<TSchema>>({ ...options, resolver:zodResolver(schema) });
}
