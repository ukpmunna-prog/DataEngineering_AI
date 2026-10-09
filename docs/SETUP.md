# Setup runbook: everything deploys through the pipeline

Only the items in section 1 are done by hand, once. After that no one creates or changes anything in
AWS by hand: every change is a pull request, a plan review and a pipeline deploy.

## 1. One-time steps (cannot be done by the pipeline itself)

| # | Step | Why it cannot be automated |
|---|---|---|
| 1 | Run the bootstrap stack with admin credentials (section 2) | The pipeline needs a role and a state bucket before it can run |
| 2 | Add the repository and environment settings in GitHub (section 3) | These live in GitHub, not AWS |
| 3 | Request Bedrock model access for Claude (and Titan Text Embeddings v2 if it is not on by default) in the AWS console | Anthropic models need a one-time use-case acceptance per account |
| 4 | Put the ServiceNow API user in GitHub as environment secrets | The pipeline copies them into Secrets Manager; they never enter git or Terraform state |

## 2. Bootstrap (once per AWS account)

```
aws cloudformation deploy \
  --stack-name sn-change-intel-bootstrap \
  --template-file infra/bootstrap/bootstrap.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --region eu-north-1 \
  --parameter-overrides GitHubOrg=<your-org> GitHubRepo=<your-repo>

aws cloudformation describe-stacks --stack-name sn-change-intel-bootstrap \
  --query "Stacks[0].Outputs" --region eu-north-1
```

If the account already has the GitHub OIDC provider, add `CreateOidcProvider=false`.
It creates: the Terraform state bucket, the GitHub OIDC trust, a read-only plan role for pull requests,
and one deploy role per environment (dev, prod). Each deploy role can only be assumed by a job running in
the matching GitHub Environment, and can only touch resources named `sn-change-intel-<env>-*`.

## 3. GitHub settings

Repository variables (Settings, Secrets and variables, Actions, Variables):

| Name | Value |
|---|---|
| `AWS_REGION` | `eu-north-1` (or your region) |
| `TF_STATE_BUCKET` | stack output `StateBucket` |
| `AWS_PLAN_ROLE_ARN` | stack output `PlanRoleArn` |

Environments (Settings, Environments): create `dev` and `prod`.

| Environment | Variable | Secrets | Protection |
|---|---|---|---|
| `dev` | `AWS_ROLE_ARN` = `DevDeployRoleArn` | `SN_USERNAME`, `SN_PASSWORD` | none |
| `prod` | `AWS_ROLE_ARN` = `ProdDeployRoleArn` | `SN_USERNAME`, `SN_PASSWORD` | required reviewers |

Branch protection on `main`: require pull requests and the `ci` checks (`python`, `infra`, `secrets`).

## 4. Configure and ship

1. Edit `infra/envs/dev/dev.tfvars` and `infra/envs/prod/prod.tfvars`: `sn_instance_url` and `bedrock_model_id`
   (a model id or inference profile id that is enabled in your region).
2. Open a pull request. `ci` runs the checks and `terraform-plan` posts the exact AWS changes as a comment.
3. Merge. `deploy` runs the tests, applies dev, stores the ServiceNow credentials, creates the vector index
   if missing, and smoke-tests. Then it waits for a prod reviewer and repeats for prod.

## 5. Day-to-day

- Change infra or code: pull request, read the plan comment, merge.
- Add a ServiceNow table: add `config/tables/<table>.yaml` and add the name to `tables` in the module call.
- Rotate ServiceNow credentials: change the GitHub environment secrets and re-run `deploy`.
- Roll back: revert the commit on `main`; the pipeline applies the previous state.
- Remove dev: run the `destroy-dev` workflow and type `destroy`.

## 6. Known limits

- `terraform validate` and `plan` have not been run against a real account; the first pipeline run is the first real test.
  If `terraform fmt -check` fails in CI, run `terraform fmt -recursive infra` and commit.
- Commit `.terraform.lock.hcl` for each environment after the first successful run, so provider versions stay fixed.
- The vector bucket and index are created by `scripts/ensure_vector_index.sh` (idempotent), not Terraform,
  because Terraform support for S3 Vectors depends on the provider version. Move them into Terraform when your
  pinned provider includes the `s3vectors` resources.
- The deploy roles use name-scoped permissions, not full admin. If a new resource type is added to the modules,
  extend the role policy in `infra/bootstrap/bootstrap.yaml` and re-deploy the bootstrap stack.
- Region is eu-north-1 (Stockholm). Titan Text Embeddings v2 runs in-region there; Claude is reached through an EU cross-region inference profile, so set `bedrock_model_id` to the profile id (starts with `eu.`) shown in the Bedrock console under Inference profiles. Always use the same region in the CloudShell, the `AWS_REGION` variable and the tfvars.
- Prod and dev share one AWS account here. For a bank-grade setup use separate accounts and deploy the bootstrap in each.
