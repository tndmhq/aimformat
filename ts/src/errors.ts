/** Base class for every error the reader throws. */
export class AimError extends Error {
  constructor(message: string) {
    super(message);
    this.name = new.target.name;
  }
}

/** The input is not parseable as a canonical .aim document. */
export class AimParseError extends AimError {}
