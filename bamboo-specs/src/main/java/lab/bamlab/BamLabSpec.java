package lab.bamlab;

import com.atlassian.bamboo.specs.api.BambooSpec;
import com.atlassian.bamboo.specs.api.builders.BambooKey;
import com.atlassian.bamboo.specs.api.builders.Variable;
import com.atlassian.bamboo.specs.api.builders.plan.Job;
import com.atlassian.bamboo.specs.api.builders.plan.Plan;
import com.atlassian.bamboo.specs.api.builders.plan.PlanIdentifier;
import com.atlassian.bamboo.specs.api.builders.plan.Stage;
import com.atlassian.bamboo.specs.api.builders.plan.dependencies.Dependencies;
import com.atlassian.bamboo.specs.api.builders.project.Project;
import com.atlassian.bamboo.specs.api.builders.repository.VcsRepositoryIdentifier;
import com.atlassian.bamboo.specs.api.builders.requirement.Requirement;
import com.atlassian.bamboo.specs.builders.task.CheckoutItem;
import com.atlassian.bamboo.specs.builders.task.ScriptTask;
import com.atlassian.bamboo.specs.builders.task.TestParserTask;
import com.atlassian.bamboo.specs.builders.task.VcsCheckoutTask;
import com.atlassian.bamboo.specs.util.BambooServer;
import java.util.List;
import lab.shared.SpecConstants;

/**
 * BAMLAB: synthetic plans with known outcomes, for trying bam (the Bamboo CLI)
 * against a real Bamboo. See lab/bamlab/README.md and
 * docs/superpowers/specs/2026-10-11-bamlab-test-plans-design.md.
 *
 * <p>Every job runs lab_step.py; its flags are the whole behaviour. No plan has
 * a trigger: they run when bam (or a person) runs them, or when a parent
 * succeeds.
 */
@BambooSpec
public class BamLabSpec {

    static final String STEP = "bamboo-specs/src/main/java/lab/bamlab/scripts/lab_step.py";
    static final String PROJECT_KEY = "BAMLAB";

    private static Project project() {
        return new Project().key(new BambooKey(PROJECT_KEY)).name("bam test lab");
    }

    private static Plan plan(String key, String name, String description) {
        return new Plan(project(), name, new BambooKey(key))
                .description(description)
                .linkedRepositories(new VcsRepositoryIdentifier().name(SpecConstants.REPO_NAME));
    }

    private static PlanIdentifier child(String key) {
        return new PlanIdentifier(PROJECT_KEY, key);
    }

    /** A job that checks the repo out, then runs lab_step.py with these flags. */
    private static Job job(String name, String key, String flags) {
        return new Job(name, new BambooKey(key)).tasks(
                new VcsCheckoutTask().description("checkout")
                        .checkoutItems(new CheckoutItem().defaultRepository()),
                new ScriptTask().description("lab step")
                        .inlineBody(STEP + " --plan ${bamboo.planKey} " + flags));
    }

    /**
     * A job that also writes JUnit results. The parser is a final task so it
     * still reads them after the step has failed the job.
     */
    private static Job testJob(String name, String key, String flags) {
        return job(name, key, flags + " --junit-dir lab-results")
                .finalTasks(TestParserTask.createJUnitParserTask()
                        .resultDirectories("lab-results/*.xml"));
    }

    Plan leaf() {
        return plan("LEAF", "Leaf", "Grandchild of TREE through OK; passes in ~5s")
                .stages(new Stage("Run").jobs(job("Run", "RUN", "--sleep 5 --tick 1")));
    }

    Plan ok() {
        return plan("OK", "OK", "Child of TREE; passes with green tests and triggers LEAF")
                .stages(
                        new Stage("Build").jobs(job("Build", "BUILD", "--sleep 10 --tick 2")),
                        new Stage("Test").jobs(testJob("Test", "TEST", "--sleep 10 --tick 2 --junit pass")))
                .dependencies(new Dependencies().childPlans(child("LEAF")));
    }

    Plan flaky() {
        return plan("FLAKY", "Flaky",
                "Child of TREE; each build fails its first attempt and passes when restarted")
                .stages(
                        new Stage("Build").jobs(job("Build", "BUILD", "--sleep 5 --tick 1")),
                        new Stage("Test").jobs(
                                testJob("Test", "TEST", "--sleep 5 --tick 1 --junit auto "
                                        + "--outcome first-attempt-fails "
                                        + "--attempt-key ${bamboo.buildResultKey}")
                                        // The attempt marker lives in the agent's home; the
                                        // host agent's home persists across restarts, the
                                        // containerized agent's may not.
                                        .requirements(new Requirement("agent.role")
                                                .matchValue("host")
                                                .matchType(Requirement.MatchType.EQUALS))));
    }

    Plan slow() {
        return plan("SLOW", "Slow", "Child of TREE; three one-minute stages with a live log")
                .stages(
                        new Stage("Stage 1").jobs(job("Stage 1", "ONE", "--sleep 60 --tick 2")),
                        new Stage("Stage 2").jobs(job("Stage 2", "TWO", "--sleep 60 --tick 2")),
                        new Stage("Stage 3").jobs(job("Stage 3", "THREE", "--sleep 60 --tick 2")));
    }

    Plan orphan() {
        return plan("ORPHAN", "Orphan",
                "Never triggered; listed under TREE in the bam children config to show as not run")
                .stages(new Stage("Run").jobs(job("Run", "RUN", "--sleep 3 --tick 1")));
    }

    Plan vars() {
        return plan("VARS", "Vars", "Prints the variables it received; the outcome variable decides pass or fail")
                .variables(
                        new Variable("env", "dev"),
                        new Variable("release", ""),
                        // Bamboo masks it by name; never a real secret.
                        new Variable("db_password", "changeme"),
                        new Variable("outcome", "pass"))
                .stages(new Stage("Run").jobs(
                        job("Run", "RUN", "--show-vars --outcome \"${bamboo.outcome}\"")));
    }

    Plan fail() {
        return plan("FAIL", "Fail", "Always fails with an error tail and no tests")
                .stages(new Stage("Run").jobs(job("Run", "RUN", "--sleep 3 --tick 1 --outcome fail")));
    }

    Plan tree() {
        return plan("TREE", "Tree", "Parent: on success triggers OK, FLAKY and SLOW")
                .stages(
                        new Stage("Prepare").jobs(job("Prepare", "PREP", "--sleep 4 --tick 2")),
                        new Stage("Fan-out").jobs(
                                job("Left", "LEFT", "--sleep 6 --tick 2"),
                                job("Right", "RIGHT", "--sleep 6 --tick 2")))
                .dependencies(new Dependencies().childPlans(
                        child("OK"), child("FLAKY"), child("SLOW")));
    }

    /** Publish order: Bamboo refuses a dependency on a plan that does not exist yet. */
    List<Plan> plans() {
        return List.of(leaf(), ok(), flaky(), slow(), orphan(), vars(), fail(), tree());
    }

    public static void main(String[] args) {
        BambooServer server = new BambooServer(SpecConstants.BAMBOO_URL);
        for (Plan plan : new BamLabSpec().plans()) {
            server.publish(plan);
        }
    }
}
