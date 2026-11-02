package mo.must.process;

import lombok.Data;
import lombok.experimental.Accessors;

@Data
@Accessors(chain = true)
public class InvalidPrompt {
    private String pageName;
    private Integer pageId;
    private Integer isIcse;
    private String appName;
    private String globalPrompt;
    private String componentPrompt;
    private String relatedPrompt;
    private String restrictivePrompt;
    private String guidingPrompt;
    private Integer componentCount;
    private String bugDescription;
    private Integer isCrash;
    private Integer isInvalid;
}
