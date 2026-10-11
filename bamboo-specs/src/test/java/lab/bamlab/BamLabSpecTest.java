package lab.bamlab;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import com.atlassian.bamboo.specs.api.model.plan.JobProperties;
import com.atlassian.bamboo.specs.api.model.plan.PlanProperties;
import com.atlassian.bamboo.specs.api.model.plan.dependencies.DependenciesProperties;
import com.atlassian.bamboo.specs.api.model.task.TaskProperties;
import com.atlassian.bamboo.specs.api.model.VariableProperties;
import com.atlassian.bamboo.specs.api.util.EntityPropertiesBuilders;
import com.atlassian.bamboo.specs.model.task.ScriptTaskProperties;
import com.atlassian.bamboo.specs.model.task.TestParserTaskProperties;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;
import org.junit.Test;

public class BamLabSpecTest {

    private static List<PlanProperties> built() {
        return new BamLabSpec().plans().stream()
                .map(EntityPropertiesBuilders::build)
                .collect(Collectors.toList());
    }

    private static String key(PlanProperties plan) {
        return plan.getProject().getKey().getKey() + "-" + plan.getKey().getKey();
    }

    private static Map<String, PlanProperties> byKey() {
        Map<String, PlanProperties> plans = new LinkedHashMap<>();
        for (PlanProperties plan : built()) {
            plans.put(key(plan), plan);
        }
        return plans;
    }

    private static Set<String> children(PlanProperties plan) {
        DependenciesProperties deps = plan.getDependenciesProperties();
        if (deps == null) {
            return Set.of();
        }
        return deps.getChildPlans().stream()
                .map(c -> c.getProjectKey().getKey() + "-" + c.getKey().getKey())
                .collect(Collectors.toSet());
    }

    private static List<JobProperties> jobs(PlanProperties plan) {
        return plan.getStages().stream()
                .flatMap(s -> s.getJobs().stream())
                .collect(Collectors.toList());
    }

    private static String scripts(JobProperties job) {
        return job.getTasks().stream()
                .filter(t -> t instanceof ScriptTaskProperties)
                .map(t -> ((ScriptTaskProperties) t).getBody())
                .collect(Collectors.joining("\n"));
    }

    @Test
    public void everyPlanIsOfflineValidAndInTheBamlabProject() {
        for (PlanProperties plan : built()) {
            assertEquals("BAMLAB", plan.getProject().getKey().getKey());
            assertEquals("bam test lab", plan.getProject().getName());
        }
    }

    @Test
    public void publishOrderIsLeavesFirst() {
        assertEquals(
                List.of("BAMLAB-LEAF", "BAMLAB-OK", "BAMLAB-FLAKY", "BAMLAB-SLOW",
                        "BAMLAB-ORPHAN", "BAMLAB-VARS", "BAMLAB-FAIL", "BAMLAB-TREE"),
                built().stream().map(BamLabSpecTest::key).collect(Collectors.toList()));
    }

    @Test
    public void everyChildIsPublishedBeforeItsParent() {
        // Bamboo refuses a dependency on a plan that does not exist yet.
        List<PlanProperties> plans = built();
        List<String> order = plans.stream().map(BamLabSpecTest::key).collect(Collectors.toList());
        for (int i = 0; i < plans.size(); i++) {
            for (String child : children(plans.get(i))) {
                int at = order.indexOf(child);
                assertTrue(child + " must be published before " + order.get(i), at >= 0 && at < i);
            }
        }
    }

    @Test
    public void treeAndOkTriggerExactlyTheirChildren() {
        Map<String, PlanProperties> plans = byKey();
        assertEquals(Set.of("BAMLAB-OK", "BAMLAB-FLAKY", "BAMLAB-SLOW"),
                children(plans.get("BAMLAB-TREE")));
        assertEquals(Set.of("BAMLAB-LEAF"), children(plans.get("BAMLAB-OK")));
        for (String other : List.of("BAMLAB-LEAF", "BAMLAB-FLAKY", "BAMLAB-SLOW",
                "BAMLAB-ORPHAN", "BAMLAB-VARS", "BAMLAB-FAIL")) {
            assertEquals(other + " triggers nothing", Set.of(), children(plans.get(other)));
        }
    }

    @Test
    public void nothingTriggersAPlanByItself() {
        for (PlanProperties plan : built()) {
            assertTrue(key(plan) + " must have no trigger", plan.getTriggers().isEmpty());
        }
    }

    @Test
    public void onlyFlakysTestJobNeedsTheHostAgent() {
        for (PlanProperties plan : built()) {
            for (JobProperties job : jobs(plan)) {
                boolean flakyTest = key(plan).equals("BAMLAB-FLAKY") && job.getName().equals("Test");
                if (flakyTest) {
                    assertEquals(1, job.getRequirements().size());
                    assertEquals("agent.role", job.getRequirements().get(0).getKey());
                    assertEquals("host", job.getRequirements().get(0).getMatchValue());
                } else {
                    assertTrue(key(plan) + "/" + job.getName() + " must run on any agent",
                            job.getRequirements().isEmpty());
                }
            }
        }
    }

    @Test
    public void junitParserIsAFinalTaskOnlyWhereResultsAreWritten() {
        Set<String> withParser = Set.of("BAMLAB-OK/Test", "BAMLAB-FLAKY/Test");
        for (PlanProperties plan : built()) {
            for (JobProperties job : jobs(plan)) {
                String where = key(plan) + "/" + job.getName();
                long parsers = job.getFinalTasks().stream()
                        .filter(t -> t instanceof TestParserTaskProperties).count();
                assertEquals(where, withParser.contains(where) ? 1 : 0, parsers);
                assertTrue(where + " must not parse tests outside final tasks",
                        job.getTasks().stream().noneMatch(t -> t instanceof TestParserTaskProperties));
                assertEquals(where + " writes JUnit iff it parses it",
                        withParser.contains(where), scripts(job).contains("--junit "));
            }
        }
    }

    @Test
    public void everyJobChecksOutThenRunsTheStep() {
        for (PlanProperties plan : built()) {
            for (JobProperties job : jobs(plan)) {
                List<TaskProperties> tasks = job.getTasks();
                assertTrue(tasks.get(0).getClass().getSimpleName().startsWith("VcsCheckout"));
                assertTrue(scripts(job).startsWith(BamLabSpec.STEP + " --plan ${bamboo.planKey}"));
            }
        }
    }

    @Test
    public void flakyTestJobKeysItsMarkerOnTheBuildResultKey() {
        JobProperties test = jobs(byKey().get("BAMLAB-FLAKY")).stream()
                .filter(j -> j.getName().equals("Test")).findFirst().orElseThrow();
        String body = scripts(test);
        assertTrue(body.contains("--outcome first-attempt-fails"));
        assertTrue(body.contains("--attempt-key ${bamboo.buildResultKey}"));
        assertTrue(body.contains("--junit auto"));
    }

    @Test
    public void varsPlanDeclaresTheVariablesBamResolves() {
        Map<String, String> vars = byKey().get("BAMLAB-VARS").getVariables().stream()
                .collect(Collectors.toMap(VariableProperties::getName, VariableProperties::getValue,
                        (a, b) -> a, LinkedHashMap::new));
        assertEquals(Map.of("env", "dev", "release", "", "db_password", "changeme",
                "outcome", "pass"), vars);
        String body = jobs(byKey().get("BAMLAB-VARS")).get(0).getTasks().stream()
                .filter(t -> t instanceof ScriptTaskProperties)
                .map(t -> ((ScriptTaskProperties) t).getBody()).findFirst().orElseThrow();
        assertTrue(body.contains("--show-vars"));
        assertTrue(body.contains("--outcome \"${bamboo.outcome}\""));
    }

    @Test
    public void slowHasThreeStagesOfAMinuteEach() {
        PlanProperties slow = byKey().get("BAMLAB-SLOW");
        assertEquals(3, slow.getStages().size());
        for (JobProperties job : jobs(slow)) {
            assertTrue(scripts(job).contains("--sleep 60 --tick 2"));
        }
    }
}
