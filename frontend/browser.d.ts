/** Shared browser component hook used when saved fragments replace page content. */
interface Window {
    aranamiComponents?: { enhance(root: ParentNode): void; dispose(root: ParentNode): void };
}

declare var aranamiComponents: Window["aranamiComponents"];

declare module "*.less";
